"""Unified PowerPoint and image ingestion, visual coverage, and citations."""
import io
import os
import time
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches

from backend import app as service, documents, ingestion, vision


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "db.sqlite3"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "false")
    with TestClient(service.app) as client:
        yield client


def course(client):
    return client.post("/api/courses", json={"name": "Course"}).json()["id"]


def wait(client, cid, docid):
    for _ in range(200):
        result = client.get(f"/api/courses/{cid}/documents/{docid}").json()
        if result["status"] in ("ready", "failed"):
            return result
        time.sleep(0.02)
    raise AssertionError("Document ingestion did not finish")


def upload(client, cid, data, filename):
    return client.post(f"/api/courses/{cid}/documents", files={"file": (filename, data)})


def png_bytes():
    image = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(image).text((20, 50), "Recursion uses a base case", fill="black")
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def pptx_bytes(with_image=False):
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    shape.text = "Stacks obey last-in-first-out ordering."
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "LIFO"
    table.cell(0, 1).text = "stack"
    slide2 = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    shape.text = "A binary heap supports priority queues."
    if with_image:
        slide2.shapes.add_picture(io.BytesIO(png_bytes()), Inches(1), Inches(3), width=Inches(3))
    data = io.BytesIO()
    presentation.save(data)
    return data.getvalue()


def test_pptx_slide_search_citation_and_download(client):
    cid, second = course(client), course(client)
    response = upload(client, cid, pptx_bytes(), "module.pptx")
    assert response.status_code == 201, response.text
    doc = wait(client, cid, response.json()["id"])
    assert doc["status"] == "ready", doc
    assert doc["pages"] == 2
    assert doc["chunk_count"] >= 2
    found = client.get(f"/api/courses/{cid}/search", params={"query": "priority queues binary heap"}).json()
    assert len(found) >= 1
    assert found[0]["page"] == 2 and found[0]["extension"] == ".pptx"
    assert found[0]["source_url"].endswith("/file")
    assert client.get(f"/api/courses/{second}/search", params={"query": "heap"}).json() == []
    file_reply = client.get(f"/api/courses/{cid}/documents/{doc['id']}/file")
    assert file_reply.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.presentationml.presentation")
    assert file_reply.content.startswith(b"PK")
    assert client.delete(f"/api/courses/{cid}/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/courses/{cid}/search", params={"query": "heap"}).json() == []


def test_visual_slide_uses_full_page_renderer_not_embedded_picture_ocr(client, monkeypatch):
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "true")
    client.put("/api/settings", json={"provider": "ollama", "model": "fake-vision-model"})
    import fitz
    def renderer(source, output):
        target = output / (source.stem + ".pdf")
        rendered = fitz.open()
        for _ in Presentation(str(source)).slides:
            rendered.new_page(width=900, height=550)
        rendered.save(str(target))
        rendered.close()
        return target
    monkeypatch.setattr(ingestion, "_render_office", renderer)
    calls = []
    def fake_analyze(self, image, native, label):
        calls.append((image, native, label))
        self.used_vision += 1
        return "Visual diagram: B-tree indexing"
    monkeypatch.setattr(vision.VisionSession, "analyze", fake_analyze)
    cid = course(client)
    response = upload(client, cid, pptx_bytes(with_image=True), "slides.pptx")
    doc = wait(client, cid, response.json()["id"])
    assert doc["status"] == "ready", doc
    if ingestion._render_office is not None and __import__("shutil").which("soffice"):
        assert len(calls) == 2 and calls[0][2] == "slide 1" and calls[1][2] == "slide 2"
        assert "Stacks" in calls[0][1]
        found = client.get(f"/api/courses/{cid}/search", params={"query": "B-tree indexing"}).json()
        assert found and found[0]["page"] in (1,2)
        coverage = client.get(f"/api/courses/{cid}/documents/{doc['id']}/coverage").json()
        assert coverage["processed"] == 2 and coverage["vision"] == 2


def test_png_uses_selected_vision_model_not_ocr(client, monkeypatch):
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "true")
    client.put("/api/settings", json={"provider": "ollama", "model": "multimodal-test"})
    seen = []
    def fake_analyze(self, image, native, label):
        seen.append((image, label, self.settings))
        return "Mitosis has four named phases"
    monkeypatch.setattr(vision.VisionSession, "analyze", fake_analyze)
    cid = course(client)
    reply = upload(client, cid, png_bytes(), "biology.png")
    doc = wait(client, cid, reply.json()["id"])
    assert doc["status"] == "ready", doc
    found = client.get(f"/api/courses/{cid}/search", params={"query": "Mitosis"}).json()
    assert found and found[0]["extension"] == ".png"
    assert seen and seen[0][2]["model"] == "multimodal-test"
    assert client.get(f"/api/courses/{cid}/documents/{doc['id']}/file").headers["content-type"].startswith("image/png")
    assert upload(client, cid, png_bytes(), "incorrect.jpg").status_code == 422


def test_invalid_corrupt_and_bomb_pptx(client):
    cid = course(client)
    assert upload(client, cid, b"not-pptx", "fake.pptx").status_code == 422
    assert upload(client, cid, b"not-png", "fake.png").status_code == 422
    data = io.BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as zip_file:
        zip_file.writestr("ppt/presentation.xml", b"a" * (101 * 1024 * 1024))
        zip_file.writestr("[Content_Types].xml", b"stub")
    assert upload(client, cid, data.getvalue(), "bomb.pptx").status_code == 422


def test_image_without_vision_fails_with_actionable_message(client):
    cid = course(client)
    reply = upload(client, cid, png_bytes(), "diagram.png")
    doc = wait(client, cid, reply.json()["id"])
    assert doc["status"] == "failed"
    assert "vision-capable" in doc["error"]
    assert client.get(f"/api/courses/{cid}/search", params={"query": "diagram"}).json() == []


def test_pptx_native_text_survives_unavailable_vision(client, monkeypatch):
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "true")
    client.put("/api/settings", json={"provider": "ollama", "model": "fake-vision-model"})
    def model_down(self, image, native, label):
        self.disabled_reason = "Vision service unavailable"
        return None
    monkeypatch.setattr(vision.VisionSession, "analyze", model_down)
    cid = course(client)
    response = upload(client, cid, pptx_bytes(with_image=True), "mixed.pptx")
    indexed = wait(client, cid, response.json()["id"])
    assert indexed["status"] == "ready"
    assert "Vision service unavailable" in indexed["warning"]
    assert client.get(f"/api/courses/{cid}/search", params={"query": "priority queues"}).json()


def test_presentation_coverage_is_bounded_and_spreads_across_slides(client):
    cid = course(client)
    presentation = Presentation()
    for number in range(1, 21):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(3))
        shape.text = f"Slide {number} " + "Detailed supply and demand analysis. " * 65
    stream = io.BytesIO()
    presentation.save(stream)
    response = upload(client, cid, stream.getvalue(), "economics.pptx")
    doc_id = response.json()["id"]
    assert wait(client, cid, doc_id)["status"] == "ready"
    excerpts, coverage = documents.ordered_document_coverage(cid, [doc_id], max_sources=5, max_chars=1000)
    assert len(excerpts) == 5
    assert sum(len(e["snippet"]) for e in excerpts) <= 1000
    assert excerpts[0]["page"] == 1 and excerpts[-1]["page"] == 20
    assert coverage[0]["slides_with_indexed_text"] == 20
    assert coverage[0]["slides_in_context"] == 5
    assert coverage[0]["excerpt_truncated"] is True


def test_native_pptx_chart_keeps_categories_and_axes(client):
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    cid = course(client)
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    data = CategoryChartData()
    data.categories = ["10 dollars", "20 dollars", "30 dollars"]
    data.add_series("Quantity supplied", (12, 24, 36))
    graphic = slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(1), Inches(1), Inches(7), Inches(4), data)
    graphic.chart.category_axis.has_title = True
    graphic.chart.category_axis.axis_title.text_frame.text = "Price"
    graphic.chart.value_axis.has_title = True
    graphic.chart.value_axis.axis_title.text_frame.text = "Quantity"
    stream = io.BytesIO()
    deck.save(stream)
    uploaded = upload(client, cid, stream.getvalue(), "native-graph.pptx")
    assert wait(client, cid, uploaded.json()["id"])["status"] == "ready"
    found = client.get(f"/api/courses/{cid}/search", params={"query": "supplied"}).json()
    assert found and "Category axis: Price" in found[0]["snippet"]
    assert "Value axis: Quantity" in found[0]["snippet"]
    assert "20 dollars: 24" in found[0]["snippet"]
