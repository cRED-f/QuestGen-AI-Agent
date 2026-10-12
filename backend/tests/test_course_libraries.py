"""Library selection is durable and never crosses the course boundary."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import app as service, documents, generation


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "course-library.sqlite"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("QUESTGEN_EMBED_MODEL", raising=False)
    with TestClient(service.app) as test_client:
        test_client.put("/api/settings", json={"provider": "ollama", "model": "qwen3:8b"})
        yield test_client


def new_course(client, name):
    return client.post("/api/courses", json={"name": name}).json()["id"]


def new_thread(client, course_id):
    return client.post("/api/threads", json={"course_id": course_id}).json()["id"]


def new_document(client, course_id, filename, content):
    response = client.post(f"/api/courses/{course_id}/documents", files={"file": (filename, content.encode())})
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    for _ in range(200):
        state = client.get(f"/api/courses/{course_id}/documents/{doc_id}").json()
        if state["status"] in {"ready", "failed"}:
            assert state["status"] == "ready", state
            return doc_id
        time.sleep(0.01)
    pytest.fail("Document indexing timed out")


def stream(client, thread_id, data):
    response = client.post(f"/api/threads/{thread_id}/stream", json=data)
    assert response.status_code == 200, response.text
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines()
            if line.startswith("data: ")]


def install_llm(monkeypatch):
    async def tokens(settings, history):
        yield "Grounded response based on chosen document."
    monkeypatch.setattr(service, "generate_tokens", tokens)


def test_course_library_isolated_and_selective_retrieval(client):
    a, b = new_course(client, "History"), new_course(client, "Physics")
    a1 = new_document(client, a, "history.txt", "Plato discussed ideas and philosophy in Athens.")
    a2 = new_document(client, a, "other.txt", "Plato discussed Athens and ancient history with scholars.")
    b1 = new_document(client, b, "physics.txt", "Plato is not the physics lecture; quantum theory is covered.")
    assert {item["id"] for item in client.get(f"/api/courses/{a}/documents").json()} == {a1, a2}
    assert [item["id"] for item in client.get(f"/api/courses/{b}/documents").json()] == [b1]
    results = documents.retrieve(a, "Plato Athens", 6, [a1])
    assert {item["document_id"] for item in results} == {a1}
    assert documents.retrieve(a, "Plato Athens", 6, [b1]) == []
    assert documents.retrieve(a, "Plato Athens", 6, []) == []
    assert {item["document_id"] for item in documents.retrieve(a, "Plato Athens", 6)} == {a1, a2}


def test_attach_is_saved_and_regeneration_preserves_selection(client, monkeypatch):
    install_llm(monkeypatch)
    c = new_course(client, "Algorithms")
    first = new_document(client, c, "sorting.txt", "A merge sort algorithm divides an array to sort data.")
    second = new_document(client, c, "linked.txt", "A linked list is a sequence of nodes with pointers.")
    thread = new_thread(client, c)
    events = stream(client, thread, {"content": "Explain sort and list algorithms", "document_ids": [first]})
    assert {s["document_id"] for s in events[1]["sources"]} == {first}
    user = client.get(f"/api/threads/{thread}/messages").json()[0]
    assert [d["id"] for d in user["documents"]] == [first]
    assistant = client.get(f"/api/threads/{thread}/messages").json()[-1]
    replay = stream(client, thread, {"regenerate_message_id": assistant["id"]})
    assert {s["document_id"] for s in replay[1]["sources"]} == {first}
    assert [d["id"] for d in client.get(f"/api/threads/{thread}/messages").json()[0]["documents"]] == [first]
    edit = stream(client, thread, {"edit_message_id": user["id"], "content": "Explain linked list nodes", "document_ids": [second]})
    assert {s["document_id"] for s in edit[1]["sources"]} == {second}
    assert [d["id"] for d in client.get(f"/api/threads/{thread}/messages").json()[0]["documents"]] == [second]


def test_cross_course_attachments_rejected_without_saving_message(client, monkeypatch):
    install_llm(monkeypatch)
    a, b = new_course(client, "A"), new_course(client, "B")
    document_a = new_document(client, a, "a.txt", "Compilers parse source code into abstract syntax trees.")
    thread_b = new_thread(client, b)
    rejected = client.post(f"/api/threads/{thread_b}/stream", json={
        "content": "Use the file from A", "document_ids": [document_a]})
    assert rejected.status_code == 422
    assert client.get(f"/api/threads/{thread_b}/messages").json() == []
    assert client.post(f"/api/threads/{thread_b}/generations", json={
        "prompt": "Create an assessment for the lecture", "document_ids": [document_a]
    }).status_code == 422
    assert client.get(f"/api/threads/{thread_b}/generations").json() == []
    assert client.post(f"/api/threads/{thread_b}/stream", json={
        "content": "Use duplicates", "document_ids": [document_a, document_a]
    }).status_code == 422


def test_deletion_cleans_up_message_attachment_and_search(client, monkeypatch):
    install_llm(monkeypatch)
    a = new_course(client, "A")
    doc = new_document(client, a, "a.txt", "The lecture explains binary trees and heaps.")
    thread = new_thread(client, a)
    stream(client, thread, {"content": "Explain trees and heaps", "document_ids": [doc]})
    assert client.delete(f"/api/courses/{a}/documents/{doc}").status_code == 204
    assert client.get(f"/api/threads/{thread}/messages").json()[0]["documents"] == []
    assert documents.retrieve(a, "binary trees", 6, [doc]) == []


def test_assessment_persists_selected_course_materials(client, monkeypatch):
    a, b = new_course(client, "A"), new_course(client, "B")
    doc_a = new_document(client, a, "a.txt", "Physics units such as newtons measure force.")
    doc_b = new_document(client, b, "b.txt", "Cell structure includes organelles and membranes.")
    tid = new_thread(client, a)
    monkeypatch.setattr(generation, "create_blueprint", lambda *args: (_ for _ in ()).throw(RuntimeError("test run")))
    response = client.post(f"/api/threads/{tid}/generations", json={
        "prompt": "Prepare a quiz about newtonian mechanics", "document_ids": [doc_a],
    })
    assert response.status_code == 201, response.text
    job = client.get(f"/api/threads/{tid}/generations/{response.json()['id']}").json()
    assert job["document_ids"] == [doc_a]
    assert doc_b not in job["document_ids"]


def test_legacy_chat_without_selection_remains_course_scoped(client, monkeypatch):
    install_llm(monkeypatch)
    a, b = new_course(client, "A"), new_course(client, "B")
    a1 = new_document(client, a, "a.txt", "Dynamic programming solves overlapping subproblems.")
    new_document(client, b, "b.txt", "Dynamic programming in Biology is not this course.")
    thread = new_thread(client, a)
    output = stream(client, thread, {"content": "Explain dynamic programming"})
    assert {s["document_id"] for s in output[1]["sources"]} == {a1}


def test_assessment_worker_retrieves_only_chosen_file(client, monkeypatch):
    a = new_course(client, "Geography")
    selected = new_document(client, a, "climate.txt", "Climate zones experience annual rainfall and seasonal temperatures.")
    excluded = new_document(client, a, "weather.txt", "Climate zones and annual rainfall also appear in this unrelated handout.")
    tid = new_thread(client, a)
    captured = []

    async def fake_blueprint(job, settings):
        return generation.AssessmentBlueprint(title="Climate quiz", total_marks=5, questions=[
            generation.QuestionPlan(topic="climate zones annual rainfall", kind="short_answer",
                                    difficulty="easy", bloom_level="understand", marks=5)])

    async def fake_generate(settings, plan, sources, previous_issue=""):
        captured.extend(sources)
        return generation.GeneratedQuestion(stem="Explain how annual rainfall influences climate zones.",
            correct_answer="Rainfall patterns help classify climate zones.",
            explanation="Rainfall and temperature are standard climate classification factors.", source_ids=[])

    async def fake_review(settings, plan, question, sources):
        return generation.QualityReview(passed=True)

    monkeypatch.setattr(generation, "create_blueprint", fake_blueprint)
    monkeypatch.setattr(generation, "generate_question", fake_generate)
    monkeypatch.setattr(generation, "review_question", fake_review)
    response = client.post(f"/api/threads/{tid}/generations", json={
        "prompt": "Write one exam question about climate zones", "document_ids": [selected],
    })
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/threads/{tid}/generations/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert job["status"] == "completed", job
    assert captured and {src["document_id"] for src in captured} == {selected}
    assert excluded not in {src["document_id"] for src in captured}


def test_invalid_cross_course_edit_keeps_original_turn(client, monkeypatch):
    install_llm(monkeypatch)
    a, b = new_course(client, "A"), new_course(client, "B")
    own = new_document(client, a, "a.txt", "Cryptography is studied using encryption keys.")
    foreign = new_document(client, b, "b.txt", "Linear algebra is studied using matrices.")
    tid = new_thread(client, a)
    stream(client, tid, {"content": "Explain encryption keys", "document_ids": [own]})
    old = client.get(f"/api/threads/{tid}/messages").json()
    rejected = client.post(f"/api/threads/{tid}/stream", json={
        "edit_message_id": old[0]["id"], "content": "Use the foreign document", "document_ids": [foreign],
    })
    assert rejected.status_code == 422
    assert client.get(f"/api/threads/{tid}/messages").json() == old
