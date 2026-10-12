"""Run: pytest backend/tests -q"""

import json

import pytest
from fastapi.testclient import TestClient

from backend import app as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(module.app) as c:
        yield c


def test_courses_threads_and_persistence(client):
    courses = client.get("/api/courses").json()
    assert len(courses) == 1 and courses[0]["name"] == "General"
    created = client.post("/api/courses", json={"name": " Algorithms "})
    assert created.status_code == 201
    course_id = created.json()["id"]
    assert created.json()["name"] == "Algorithms"
    thread = client.post("/api/threads", json={"course_id": course_id})
    assert thread.status_code == 201
    thread_id = thread.json()["id"]
    assert len(client.get("/api/threads", params={"course_id": course_id}).json()) == 1
    assert client.get(f"/api/threads/{thread_id}/messages").json() == []
    assert client.get("/api/threads", params={"course_id": "bad"}).status_code == 404
    assert client.delete(f"/api/threads/{thread_id}").status_code == 204
    assert client.get(f"/api/threads/{thread_id}/messages").status_code == 404
    assert len(client.get("/api/courses").json()) == 2


def test_settings_validation(client):
    assert client.get("/api/settings").json()["provider"] == "ollama"
    response = client.put("/api/settings", json={"provider": "openrouter", "model": "test/model"})
    assert response.status_code == 200
    assert client.get("/api/settings").json() == {"provider": "openrouter", "model": "test/model"}
    assert client.put("/api/settings", json={"provider": "external", "model": "model"}).status_code == 422


def test_stream_and_database_history(client, monkeypatch):
    async def fake_generate(_settings, history):
        assert history[-1]["content"] == "Build a quiz"
        yield "Here is "
        yield "a quiz."

    monkeypatch.setattr(module, "generate_tokens", fake_generate)
    client.put("/api/settings", json={"provider": "ollama", "model": "qwen3:8b"})
    thread_id = client.post("/api/threads", json={"course_id": client.get("/api/courses").json()[0]["id"]}).json()["id"]
    events = []
    with client.stream("POST", f"/api/threads/{thread_id}/stream", json={"content": "Build a quiz"}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert [event["type"] for event in events] == ["started", "sources", "delta", "delta", "done"]
    messages = client.get(f"/api/threads/{thread_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "Here is a quiz."
    threads = client.get("/api/threads", params={"course_id": client.get("/api/courses").json()[0]["id"]}).json()
    assert threads[0]["title"] == "Build a quiz"


def test_no_key_does_not_persist_failed_request(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client.put("/api/settings", json={"provider": "openrouter", "model": "anything"})
    thread_id = client.post("/api/threads", json={"course_id": client.get("/api/courses").json()[0]["id"]}).json()["id"]
    assert client.post(f"/api/threads/{thread_id}/stream", json={"content": "Hello"}).status_code == 422
    assert client.get(f"/api/threads/{thread_id}/messages").json() == []


def test_stream_surfaces_model_reasoning_without_saving_it(client, monkeypatch):
    """Thinking-model reasoning arrives as SSE events but never enters history."""
    async def fake_generate(_settings, history):
        yield module.ModelChunk("Sorting the options by cost. ", "reasoning")
        yield module.ModelChunk("Breadth-first search fits best. ", "reasoning")
        yield "The answer is breadth-first search."

    monkeypatch.setattr(module, "generate_tokens", fake_generate)
    client.put("/api/settings", json={"provider": "ollama", "model": "qwen3:8b"})
    thread_id = client.post("/api/threads", json={"course_id": client.get("/api/courses").json()[0]["id"]}).json()["id"]
    with client.stream("POST", f"/api/threads/{thread_id}/stream", json={"content": "Explain BFS"}) as response:
        assert response.status_code == 200
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert [event["type"] for event in events] == ["started", "sources", "reasoning", "reasoning", "delta", "done"]
    assert events[2]["content"].startswith("Sorting the options")
    assert events[4]["content"] == "The answer is breadth-first search."
    messages = client.get(f"/api/threads/{thread_id}/messages").json()
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "The answer is breadth-first search."
