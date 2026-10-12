"""Regression coverage for the new unified ingestion dispatcher and selected-model vision."""
from __future__ import annotations
import io
import time

import fitz
import pytest
from docx import Document
from docx.shared import Inches
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from backend import app as service, documents, vision


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "work.sqlite"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "true")
    with TestClient(service.app) as api:
        api.put("/api/settings", json={"provider": "ollama", "model": "test-vision"})
        yield api


def upload(client, name, data):
    cid = client.get("/api/courses").json()[0]["id"]
    response = client.post(f"/api/courses/{cid}/documents", files={"file": (name, data)})
    assert response.status_code == 201, response.text
    docid = response.json()["id"]
    for _ in range(200):
        row = client.get(f"/api/courses/{cid}/documents/{docid}").json()
        if row["status"] in ("ready", "failed"):
            return cid, docid, row
        time.sleep(.02)
    raise AssertionError("Document did not finish")


def test_pdf_text_and_scanned_page_are_both_grounded(client, monkeypatch):
    calls = []
    def fake_vision(self, image, native, label):
        calls.append((native, label))
        return "Visual diagram: opportunity cost rises with more output"
    monkeypatch.setattr(vision.VisionSession, "analyze", fake_vision)
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(60, 710, "All resources are scarce in an economy.")
    pdf.showPage()
    art = Image.new("RGB", (180, 130), "white")
    pdf.drawImage(ImageReader(art), 60, 480, width=350, height=260)
    pdf.showPage()
    pdf.save()
    cid, docid, record = upload(client, "economics.pdf", output.getvalue())
    assert record["status"] == "ready"
    assert record["pages"] == 2
    assert len(calls) >= 1 and any(label == "page 2" for _, label in calls)
    coverage = client.get(f"/api/courses/{cid}/documents/{docid}/coverage").json()
    assert coverage["processed"] == 2 and coverage["vision"] >= 1
    snippets, summary = documents.ordered_document_coverage(cid, [docid])
    assert {source["page"] for source in snippets} == {1, 2}
    assert summary[0]["total_pages"] == 2
    assert any("opportunity cost" in s["snippet"] for s in snippets)


def test_docx_tables_keep_document_order_and_images_get_vision(client, monkeypatch):
    labels = []
    def fake_vision(self, _image, native, label):
        labels.append(label)
        return "Diagram displays a linked list with three nodes"
    monkeypatch.setattr(vision.VisionSession, "analyze", fake_vision)
    d = Document()
    d.add_heading("Lecture Notes", 1)
    d.add_paragraph("Introduction to data structures.")
    t = d.add_table(rows=1, cols=2)
    t.cell(0,0).text = "Operation"
    t.cell(0,1).text = "Time"
    d.add_paragraph("Conclusion: linked lists grow dynamically.")
    img = Image.new("RGB", (80, 50), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    d.add_picture(io.BytesIO(buf.getvalue()), width=Inches(1))
    file = io.BytesIO()
    d.save(file)
    cid, docid, record = upload(client, "lecture.docx", file.getvalue())
    assert record["status"] == "ready", record
    assert labels and labels[0].startswith("image")
    sources, _ = documents.ordered_document_coverage(cid, [docid])
    text = "\n".join(s["snippet"] for s in sources)
    assert text.index("Introduction") < text.index("Operation | Time") < text.index("Conclusion")
    assert "three nodes" in text
    assert all(src["section"] == "Lecture Notes" for src in sources)


def test_markdown_preserves_headings_tables_and_fenced_code(client, monkeypatch):
    def should_not_invoke(*args, **kwargs):
        raise AssertionError("Plain Markdown must not invoke a vision model")
    monkeypatch.setattr(vision.VisionSession, "analyze", should_not_invoke)
    md = b"# Chapter 4\nIntro paragraph.\n\n| Column | Value |\n| --- | --- |\n| X | Y |\n\n```python\nif True:\n    print('hello')\n```\n\n## Advanced\nAdvanced concept."
    cid, docid, record = upload(client, "chapter.md", md)
    assert record["status"] == "ready", record
    sources, _ = documents.ordered_document_coverage(cid, [docid])
    assert sources[0]["section"] == "Chapter 4"
    assert sources[-1]["section"] == "Advanced"
    assert "    print('hello')" in sources[0]["snippet"]
    assert "| X | Y |" in sources[0]["snippet"]


def test_selected_model_openai_compatible_image_message_and_cache(client, monkeypatch):
    requests = []
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "A PPF curve with labeled axes"}}]}
    class FakeClient:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self, url, **kwargs):
            requests.append((url,kwargs))
            return FakeResponse()
    monkeypatch.setattr(vision.httpx, "Client", FakeClient)
    img = Image.new("RGB", (60, 60), "white")
    stream = io.BytesIO()
    img.save(stream, "PNG")
    session = vision.VisionSession("abcedf")
    assert session.analyze(stream.getvalue(), "Price and quantity", "slide 7") == "A PPF curve with labeled axes"
    again = vision.VisionSession("abcedf")
    assert again.analyze(stream.getvalue(), "Price and quantity", "slide 7") == "A PPF curve with labeled axes"
    assert len(requests) == 1
    request = requests[0][1]["json"]
    assert request["model"] == "test-vision"
    assert request["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_named_pdf_uses_ordered_coverage_without_cross_document_leak(client, monkeypatch):
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "false")
    cid, docid, record = upload(client, "Chapter 2.md", b"# Topic\nPPF moves with scarcity and choice")
    assert record["status"] == "ready"
    _, other, record2 = upload(client, "Chapter 3.md", b"# Topic\nPrices adjust to demand")
    assert record2["status"] == "ready"
    ready = [{"id":docid,"filename":"Chapter 2.md","extension":".md","status":"ready"},
             {"id":other,"filename":"Chapter 3.md","extension":".md","status":"ready"}]
    assert documents.requested_document_ids("explain chapter 2", ready) == [docid]
    sources,_ = documents.ordered_document_coverage(cid, [docid])
    assert len(sources) == 1 and sources[0]["document_id"] == docid
