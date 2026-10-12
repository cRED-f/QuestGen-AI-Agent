"""Permanently deleting a course cleans storage without crossing course boundaries."""
import hashlib
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as service, documents, generation


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    # Checkpoint garbage collection must stay inside the test's data directory.
    monkeypatch.setattr(service, "ROOT", tmp_path)
    with TestClient(service.app) as c:
        yield c


def course(client, name):
    response = client.post("/api/courses", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def document(client, course_id, name, content):
    uploaded = client.post(f"/api/courses/{course_id}/documents", files={"file": (name, content.encode())})
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["id"]
    for _ in range(200):
        result = client.get(f"/api/courses/{course_id}/documents/{doc_id}").json()
        if result["status"] == "ready":
            return doc_id
        if result["status"] == "failed":
            pytest.fail(result["error"])
        time.sleep(0.01)
    pytest.fail("Timed out awaiting document")


def test_delete_course_cascades_files_chats_assessments_and_fts_preserving_other_course(client, tmp_path):
    doomed = course(client, "Delete me")
    retained = course(client, "Keep me")
    common = "Magnetic flux density is measured in teslas."
    own_doc = document(client, doomed, "lecture.txt", common)
    second_doc = document(client, doomed, "private.md", "Exclusive spectroscopy observations.")
    other_doc = document(client, retained, "copy.txt", common)
    doc_paths = [documents.document_path(own_doc, ".txt"), documents.document_path(second_doc, ".md")]
    assert all(path.exists() for path in doc_paths)
    shared_sha = hashlib.sha256(common.encode()).hexdigest()
    private_sha = hashlib.sha256(b"Exclusive spectroscopy observations.").hexdigest()
    thread_id = client.post("/api/threads", json={"course_id": doomed}).json()["id"]
    other_thread_id = client.post("/api/threads", json={"course_id": retained}).json()["id"]
    with service.database() as conn:
        chunk_id = conn.execute("SELECT id FROM document_chunks WHERE document_id=?", (own_doc,)).fetchone()["id"]
        user_id = service.identifier()
        conn.execute("INSERT INTO messages(id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)",
                     (user_id, thread_id, "user", "Explain flux", service.now()))
        conn.execute("INSERT INTO message_documents(message_id,document_id,position) VALUES (?,?,0)", (user_id, own_doc))
        conn.execute("INSERT INTO message_sources(message_id,chunk_id,position) VALUES (?,?,0)", (user_id, chunk_id))
        job_id, question_id = service.identifier(), service.identifier()
        conn.execute("INSERT INTO assessment_jobs(id,thread_id,course_id,prompt,provider,model,status,stage,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (job_id, thread_id, doomed, "Exam", "ollama", "m", "completed", "done", service.now(), service.now()))
        conn.execute("INSERT INTO assessment_questions(id,job_id,ordinal,content,created_at) VALUES (?,?,?,?,?)",
                     (question_id, job_id, 1, "{}", service.now()))
        conn.execute("INSERT INTO assessment_question_versions(question_id,version,content,change_type,instruction,created_at) VALUES (?,1,?,'generated','first',?)",
                     (question_id, "{}", service.now()))
        conn.execute("INSERT INTO assessment_question_audit(question_id,action,note,version,created_at) VALUES (?,'edit','note',1,?)",
                     (question_id, service.now()))
        conn.execute("INSERT INTO assessment_events(job_id,stage,message,progress,created_at) VALUES (?,'done','Done',100,?)",
                     (job_id, service.now()))
        conn.execute("INSERT INTO vision_cache(cache_key,document_sha,model,content) VALUES ('shared',?,'vision','text')", (shared_sha,))
        conn.execute("INSERT INTO vision_cache(cache_key,document_sha,model,content) VALUES ('private',?,'vision','text')", (private_sha,))
    # Realistic LangGraph checkpoint schema, stored independently of application DB.
    cp = tmp_path / "data" / "deepagents-checkpoints.sqlite3"
    cp.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cp) as conn:
        conn.execute("CREATE TABLE checkpoints(thread_id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO checkpoints VALUES (?, 'deleted')", (f"questgen-plan-{job_id}",))
        conn.execute("INSERT INTO checkpoints VALUES ('unrelated-job', 'keep')")
    assert client.delete(f"/api/courses/{doomed}").status_code == 204
    assert client.get(f"/api/courses/{doomed}/documents").status_code == 404
    assert client.get(f"/api/threads/{thread_id}/messages").status_code == 404
    assert client.delete(f"/api/courses/{doomed}").status_code == 404
    assert all(not path.exists() for path in doc_paths)
    assert documents.document_path(other_doc, ".txt").exists()
    assert client.get(f"/api/threads/{other_thread_id}/messages").status_code == 200
    assert len(client.get(f"/api/courses/{retained}/documents").json()) == 1
    assert any(r["document_id"] == other_doc for r in documents.retrieve(retained, "magnetic flux"))
    with service.database() as conn:
        for table in ("documents", "document_chunks", "threads", "assessment_jobs"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE course_id=?", (doomed,)).fetchone()[0] == 0
        assert not conn.execute("SELECT 1 FROM document_units WHERE document_id=?", (own_doc,)).fetchone()
        for table, key in (("messages", "thread_id"), ("message_sources", "message_id"), ("message_documents", "message_id"),
                           ("assessment_questions", "job_id"), ("assessment_events", "job_id"),
                           ("assessment_question_versions", "question_id"), ("assessment_question_audit", "question_id")):
            value = thread_id if table == "messages" else user_id if key == "message_id" else question_id if key == "question_id" else job_id
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {key}=?", (value,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        assert conn.execute("SELECT 1 FROM vision_cache WHERE cache_key='shared'").fetchone()
        assert not conn.execute("SELECT 1 FROM vision_cache WHERE cache_key='private'").fetchone()
    with sqlite3.connect(cp) as conn:
        assert [r[0] for r in conn.execute("SELECT thread_id FROM checkpoints")] == ["unrelated-job"]


def test_deleting_final_course_does_not_recreate_it_on_restart(client):
    ids = [c["id"] for c in client.get("/api/courses").json()]
    for id in ids:
        assert client.delete(f"/api/courses/{id}").status_code == 204
    assert client.get("/api/courses").json() == []
    service.init_db()
    assert client.get("/api/courses").json() == []
    new_id = course(client, "Fresh")
    assert client.get(f"/api/threads?course_id={new_id}").status_code == 200


def test_staging_failure_rolls_back_database_and_restores_prior_files(client, monkeypatch):
    doomed = course(client, "Rollback")
    first = document(client, doomed, "first.txt", "Alpha test document.")
    second = document(client, doomed, "second.txt", "Beta test document.")
    first_path = documents.document_path(first, ".txt")
    second_path = documents.document_path(second, ".txt")
    original_replace = Path.replace

    def fail_second(self, target):
        if self == second_path:
            raise OSError("Simulated file rename error")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    response = client.delete(f"/api/courses/{doomed}")
    assert response.status_code == 409
    assert "Close programs" in response.json()["detail"]
    assert first_path.exists() and second_path.exists()
    assert len(client.get(f"/api/courses/{doomed}/documents").json()) == 2
    assert documents.retrieve(doomed, "alpha test")


def test_recovery_handles_interrupted_deletion_before_and_after_db_commit(client):
    course_id = course(client, "Interrupted")
    doc_id = document(client, course_id, "recover.txt", "An important retained file.")
    original = documents.document_path(doc_id, ".txt")
    stage = documents.uploads_root() / ".course-deletions" / "crash-before-commit"
    stage.mkdir(parents=True)
    staged = stage / original.name
    original.replace(staged)
    service.recover_interrupted_course_deletions()
    assert original.exists() and not staged.exists()
    assert not stage.exists()
    # Simulate a deletion that committed but crashed before the staged file
    # could be removed.
    orphan = documents.uploads_root() / ".course-deletions" / "crash-after-commit"
    orphan.mkdir(parents=True)
    original.replace(orphan / original.name)
    with service.database() as conn:
        conn.execute("DELETE FROM courses WHERE id=?", (course_id,))
    service.recover_interrupted_course_deletions()
    assert not original.exists()
    assert not orphan.exists()


def test_cancelling_course_job_leaves_other_job_running():
    import asyncio

    async def exercise():
        gate = asyncio.Event()
        async def pending():
            await gate.wait()
        cancelled = asyncio.create_task(pending())
        retained = asyncio.create_task(pending())
        generation._ACTIVE_JOBS.update({"deleted": cancelled, "retained": retained})
        try:
            await generation.cancel_running_jobs(["deleted"])
            assert cancelled.cancelled()
            assert not retained.done()
        finally:
            gate.set()
            await retained
            generation._ACTIVE_JOBS.pop("deleted", None)
            generation._ACTIVE_JOBS.pop("retained", None)

    asyncio.run(exercise())
