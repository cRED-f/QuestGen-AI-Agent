"""Chat history actions: editing and regeneration replace later turns durably."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import app as service


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(service.app) as api:
        api.put("/api/settings", json={"provider": "ollama", "model": "test-model"})
        yield api


def thread(client):
    course = client.get("/api/courses").json()[0]["id"]
    return client.post("/api/threads", json={"course_id": course}).json()["id"]


def stream(client, tid, **payload):
    response = client.post(f"/api/threads/{tid}/stream", json=payload)
    if response.status_code != 200:
        return response, []
    return response, [json.loads(line[6:]) for line in response.text.splitlines()
                      if line.startswith("data: ")]


def messages(client, tid):
    return client.get(f"/api/threads/{tid}/messages").json()


def setup_two_turns(client, monkeypatch):
    observed = []
    async def fake_generate(settings, history):
        observed.append(history)
        yield f"Answer {len(observed)}"
    monkeypatch.setattr(service, "generate_tokens", fake_generate)
    tid = thread(client)
    for prompt in ("First question", "Second question"):
        response, events = stream(client, tid, content=prompt)
        assert response.status_code == 200 and events[-1]["type"] == "done"
    return tid, observed


def test_edit_an_older_user_message_replaces_following_chat_and_keeps_id(client, monkeypatch):
    tid, observed = setup_two_turns(client, monkeypatch)
    original = messages(client, tid)
    response, events = stream(client, tid, content="Improved first question",
                              edit_message_id=original[0]["id"])
    assert response.status_code == 200
    assert events[-1]["type"] == "done"
    updated = messages(client, tid)
    assert len(updated) == 2
    assert updated[0]["id"] == original[0]["id"]
    assert updated[0]["content"] == "Improved first question"
    assert updated[1]["id"] != original[1]["id"]
    assert observed[-1][-1] == {"role": "user", "content": "Improved first question"}
    assert all("Second question" not in str(item) for item in observed[-1])
    with service.database() as conn:
        assert not conn.execute("SELECT 1 FROM messages WHERE id=?", (original[3]["id"],)).fetchone()


def test_regenerate_an_older_assistant_reply_preserves_user_and_replaces_later_turns(client, monkeypatch):
    tid, observed = setup_two_turns(client, monkeypatch)
    original = messages(client, tid)
    response, events = stream(client, tid, regenerate_message_id=original[1]["id"])
    assert response.status_code == 200 and events[-1]["type"] == "done"
    updated = messages(client, tid)
    assert len(updated) == 2
    assert updated[0]["id"] == original[0]["id"]
    assert updated[1]["id"] != original[1]["id"]
    assert updated[1]["content"] == "Answer 3"
    assert observed[-1][-1]["content"] == "First question"
    assert len(observed[-1]) == 1


def test_invalid_action_does_not_truncate_chat(client, monkeypatch):
    tid, _ = setup_two_turns(client, monkeypatch)
    before = messages(client, tid)
    invalid = (
        {"content": "", "edit_message_id": before[0]["id"]},
        {"content": "new", "regenerate_message_id": before[1]["id"]},
        {"content": "new", "edit_message_id": before[0]["id"], "regenerate_message_id": before[1]["id"]},
    )
    for request in invalid:
        assert stream(client, tid, **request)[0].status_code == 422
    assert stream(client, tid, content="change", edit_message_id=before[1]["id"])[0].status_code == 404
    assert stream(client, tid, regenerate_message_id=before[0]["id"])[0].status_code == 404
    assert stream(client, tid, regenerate_message_id="missing")[0].status_code == 404
    other_tid = thread(client)
    assert stream(client, other_tid, content="stolen", edit_message_id=before[0]["id"])[0].status_code == 404
    assert messages(client, tid) == before
    assert messages(client, other_tid) == []


def test_failed_provider_preflight_preserves_chat(client, monkeypatch):
    tid, _ = setup_two_turns(client, monkeypatch)
    before = messages(client, tid)
    def fail_credentials(_settings):
        raise HTTPException(status_code=422, detail="No configured model")
    monkeypatch.setattr(service, "model_credentials", fail_credentials)
    assert stream(client, tid, content="edit", edit_message_id=before[0]["id"])[0].status_code == 422
    assert stream(client, tid, regenerate_message_id=before[1]["id"])[0].status_code == 422
    assert messages(client, tid) == before


def test_regenerate_latest_reply_keeps_earlier_turns(client, monkeypatch):
    tid, observed = setup_two_turns(client, monkeypatch)
    before = messages(client, tid)
    response, _ = stream(client, tid, regenerate_message_id=before[3]["id"])
    assert response.status_code == 200
    after = messages(client, tid)
    assert len(after) == 4
    assert [item["id"] for item in after[:3]] == [item["id"] for item in before[:3]]
    assert after[3]["id"] != before[3]["id"]
    assert [m["content"] for m in observed[-1]] == ["First question", "Answer 1", "Second question"]
