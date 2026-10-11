"""Phase 2 integration tests: ingestion, citations, isolation, recovery and failure handling."""
import io
import json
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from backend import app as service
from backend import documents


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "files"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    with TestClient(service.app) as client:
        yield client


def course(client, name="Class"):
    return client.post("/api/courses", json={"name": name}).json()["id"]


def upload(client, course_id, data, name="lecture.txt"):
    return client.post(f"/api/courses/{course_id}/documents", files={"file": (name, data)})


def wait_ready(client, course_id, document_id):
    for _ in range(100):
        record = client.get(f"/api/courses/{course_id}/documents/{document_id}").json()
        if record["status"] in {"ready", "failed"}:
            return record
        time.sleep(0.03)
    raise AssertionError("Document did not finish indexing")


def pdf_bytes():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(40, 750, "Binary search has logarithmic time complexity.")
    c.showPage()
    c.drawString(40, 750, "Hash tables usually offer expected constant-time lookup.")
    c.save()
    return buf.getvalue()


def test_course_scoped_upload_search_and_delete(client):
    a, b = course(client, "A"), course(client, "B")
    result = upload(client, a, b"Binary search requires sorted data and runs in logarithmic time.")
    assert result.status_code == 201
    doc = wait_ready(client, a, result.json()["id"])
    assert doc["status"] == "ready" and doc["chunk_count"] == 1
    assert doc["progress"] == 100
    found = client.get(f"/api/courses/{a}/search", params={"query": "binary search"}).json()
    assert len(found) == 1
    assert found[0]["document_id"] == doc["id"]
    assert "sorted data" in found[0]["snippet"]
    assert found[0]["source_url"].startswith("/api/workspace/courses/")
    assert client.get(f"/api/courses/{b}/search", params={"query": "binary"}).json() == []
    assert client.get(f"/api/courses/{b}/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/courses/{b}/documents/{doc['id']}/file").status_code == 404
    assert client.delete(f"/api/courses/{b}/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/courses/{a}/documents/{doc['id']}/file").content.startswith(b"Binary")
    assert client.delete(f"/api/courses/{a}/documents/{doc['id']}").status_code == 204
    assert not list(documents.uploads_root().glob("*"))
    assert client.get(f"/api/courses/{a}/search", params={"query": "binary"}).json() == []


def test_pdf_page_citations_and_dedup(client):
    cid = course(client)
    data = pdf_bytes()
    reply = upload(client, cid, data, "notes.pdf")
    assert wait_ready(client, cid, reply.json()["id"])["status"] == "ready"
    found = client.get(f"/api/courses/{cid}/search", params={"query": "hash tables lookup"}).json()
    assert found and found[0]["page"] == 2
    again = upload(client, cid, data, "renamed.pdf")
    assert again.json()["deduplicated"] is True
    assert len(client.get(f"/api/courses/{cid}/documents").json()) == 1
    assert len(list(documents.uploads_root().glob("*"))) == 1


def test_docx_and_invalid_formats(client):
    cid = course(client)
    doc = Document()
    doc.add_heading("Recursion", 1)
    doc.add_paragraph("Recursive functions need a base case and a smaller subproblem.")
    buf = io.BytesIO()
    doc.save(buf)
    uploaded = upload(client, cid, buf.getvalue(), "lecture.docx")
    assert wait_ready(client, cid, uploaded.json()["id"])["status"] == "ready"
    found = client.get(f"/api/courses/{cid}/search", params={"query": "recursive base case"}).json()
    assert found and found[0]["section"] == "Recursion"
    assert upload(client, cid, b"not a pdf", "evil.pdf").status_code == 422
    assert upload(client, cid, b"something", "fake.exe").status_code == 422
    assert upload(client, cid, b"x" * (documents.MAX_UPLOAD + 1)).status_code == 422
    assert upload(client, cid, b"\xff\xfe", "latin.txt").status_code == 422


def test_scanned_pdf_failure_and_cross_course_chat_context(client, monkeypatch):
    cid, other = course(client, "History"), course(client, "Biology")
    resource = upload(client, cid, b"Plato is mentioned in the lecture notes with Athens as a historical city.")
    assert wait_ready(client, cid, resource.json()["id"])["status"] == "ready"
    inspected = []

    async def fake_tokens(_settings, history):
        inspected.append(history)
        yield "According to the lecture, Plato was associated with Athens [1]."

    monkeypatch.setattr(service, "generate_tokens", fake_tokens)
    thread_a = client.post("/api/threads", json={"course_id": cid}).json()["id"]
    thread_b = client.post("/api/threads", json={"course_id": other}).json()["id"]
    with client.stream("POST", f"/api/threads/{thread_a}/stream", json={"content": "What does the lecture say about Plato?"}) as response:
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert [x["type"] for x in events] == ["started", "sources", "delta", "done"]
    assert len(events[1]["sources"]) == 1
    assert "Plato" in inspected[0][-2]["content"]
    messages = client.get(f"/api/threads/{thread_a}/messages").json()
    assert messages[-1]["sources"][0]["filename"] == "lecture.txt"
    with client.stream("POST", f"/api/threads/{thread_b}/stream", json={"content": "What about Plato?"}) as response:
        events_b = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events_b[1]["sources"] == []
    assert inspected[1][-1]["content"] == "What about Plato?"
    assert all("Plato is mentioned" not in entry["content"] for entry in inspected[1])
    client.delete(f"/api/courses/{cid}/documents/{resource.json()['id']}")
    assert client.get(f"/api/threads/{thread_a}/messages").json()[-1]["sources"] == []


def test_recovery_from_interrupted_indexing(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "data.sqlite3"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    service.init_db()
    cid = service.list_courses()[0]["id"]
    docid = service.identifier()
    documents.uploads_root().mkdir(parents=True, exist_ok=True)
    documents.document_path(docid, ".txt").write_text("heap sorting algorithm", encoding="utf8")
    with service.database() as conn:
        t = service.now()
        conn.execute("INSERT INTO documents(id,course_id,filename,extension,sha256,bytes,status,progress,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,?,'indexing',75,?,?)", (docid, cid, "recover.txt", ".txt", "abc", 22, t, t))
    with TestClient(service.app) as client:
        assert wait_ready(client, cid, docid)["status"] == "ready"
        assert client.get(f"/api/courses/{cid}/search", params={"query": "heap sorting"}).json()


def test_embedding_failure_falls_back_to_keyword_index(client, monkeypatch):
    monkeypatch.setenv("QUESTGEN_EMBED_MODEL", "unavailable-model")
    monkeypatch.setattr(documents, "embed_texts", lambda _items: (_ for _ in ()).throw(ValueError("model offline")))
    cid = course(client)
    item = upload(client, cid, b"Quicksort partitions arrays around a chosen pivot.")
    finished = wait_ready(client, cid, item.json()["id"])
    assert finished["status"] == "ready"
    assert "keyword search remains active" in finished["warning"]
    entries = client.get(f"/api/courses/{cid}/search", params={"query": "Quicksort pivot"}).json()
    assert len(entries) == 1


def test_search_rejects_abusive_limits(client):
    cid = course(client)
    assert client.get(f"/api/courses/{cid}/search", params={"query": "data", "limit": 100}).status_code == 422
    assert client.get(f"/api/courses/{cid}/search", params={"query": "x" * 501}).status_code == 422
    assert client.get("/api/courses/nonexistent/documents").status_code == 404
