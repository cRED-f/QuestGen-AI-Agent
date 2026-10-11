"""Phase 3 integration regression tests, with deterministic model doubles.

Real model integration requires installing Deep Agents and configuring a provider.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import app as module
from backend import generation


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "test.sqlite3"))
    with TestClient(module.app) as c:
        c.put("/api/settings", json={"provider": "ollama", "model": "qwen3:8b"})
        yield c


def thread(client):
    course_id = client.get("/api/courses").json()[0]["id"]
    return client.post("/api/threads", json={"course_id": course_id}).json()["id"]


def blueprint():
    return generation.AssessmentBlueprint(title="Data Structures Quiz", total_marks=10,
        questions=[generation.QuestionPlan(topic="binary search", kind="mcq", difficulty="medium",
                                            bloom_level="apply", marks=5),
                   generation.QuestionPlan(topic="linked lists", kind="short_answer", difficulty="easy",
                                            bloom_level="understand", marks=5)])


def install_fakes(monkeypatch, fail_second=False):
    state = {"supervisor": 0, "generated": [], "fail": fail_second}
    async def fake_plan(job, settings):
        state["supervisor"] += 1
        assert settings["provider"] == "ollama"
        return blueprint()
    async def fake_generate(settings, plan, sources, previous_issue=""):
        state["generated"].append(plan.topic)
        if state["fail"] and plan.topic == "linked lists":
            raise ValueError("forced quality failure")
        return generation.GeneratedQuestion(
            stem=f"Explain an important principle of {plan.topic} with an example.",
            options=["First", "Second", "Third", "Fourth"] if plan.kind == "mcq" else [],
            correct_answer="First" if plan.kind == "mcq" else "A suitable worked explanation.",
            explanation="This answer is correct because it follows the stated principle.",
            source_ids=[],
        )
    async def fake_review(*args, **kwargs):
        return generation.QualityReview(passed=True)
    monkeypatch.setattr(generation, "create_blueprint", fake_plan)
    monkeypatch.setattr(generation, "generate_question", fake_generate)
    monkeypatch.setattr(generation, "review_question", fake_review)
    return state


def wait_for(client, path, status):
    for _ in range(100):
        data = client.get(path).json()
        if data["status"] == status:
            return data
        time.sleep(.02)
    pytest.fail(f"Did not reach {status}: {data}")


def test_generation_persists_questions_and_replays_activity(client, monkeypatch):
    state = install_fakes(monkeypatch)
    thread_id = thread(client)
    base = f"/api/threads/{thread_id}/generations"
    created = client.post(base, json={"prompt": "Prepare two questions on data structures"})
    assert created.status_code == 201
    path = f"{base}/{created.json()['id']}"
    done = wait_for(client, path, "completed")
    assert done["blueprint"]["total_marks"] == 10
    assert [item["ordinal"] for item in done["questions"]] == [1, 2]
    assert done["questions"][0]["content"]["grounded"] is False
    assert done["questions"][0]["content"]["kind"] == "mcq"
    assert state["supervisor"] == 1
    assert client.get(base).json()[0]["id"] == done["id"]
    with client.stream("GET", path + "/events") as response:
        content = "\n".join(response.iter_lines())
    assert '"stage": "supervisor"' in content
    assert '"stage": "completed"' in content
    assert '"type": "complete"' in content
    assert client.get(path.replace(thread_id, "invalid-thread")).status_code == 404
    assert client.post(path + "/retry").status_code == 409


def test_recovery_skips_saved_questions_after_retry(client, monkeypatch):
    state = install_fakes(monkeypatch, fail_second=True)
    thread_id = thread(client)
    base = f"/api/threads/{thread_id}/generations"
    created = client.post(base, json={"prompt": "Make two data structures questions"}).json()
    path = f"{base}/{created['id']}"
    failed = wait_for(client, path, "failed")
    assert len(failed["questions"]) == 1
    assert "after 3 attempts" in failed["error"]
    assert state["generated"] == ["binary search", "linked lists", "linked lists", "linked lists"]
    state["fail"] = False
    assert client.post(path + "/retry").status_code == 200
    completed = wait_for(client, path, "completed")
    assert len(completed["questions"]) == 2
    assert state["supervisor"] == 1
    assert state["generated"].count("binary search") == 1


def test_recover_running_job_on_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "recover.sqlite"))
    state = install_fakes(monkeypatch)
    module.init_db()
    with module.database() as conn:
        cid = conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        tid = module.identifier()
        jid = module.identifier()
        t = module.now()
        conn.execute("INSERT INTO threads(id,course_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                     (tid, cid, "Resume test", t, t))
        conn.execute("INSERT INTO assessment_jobs(id,thread_id,course_id,prompt,provider,model,status,stage,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,?,'running','generating',?,?)",
                     (jid, tid, cid, "Make a data structures quiz", "ollama", "qwen3:8b", t, t))
        conn.execute("UPDATE assessment_jobs SET blueprint=? WHERE id=?", (blueprint().model_dump_json(), jid))
    with TestClient(module.app) as client:
        completed = wait_for(client, f"/api/threads/{tid}/generations/{jid}", "completed")
        assert len(completed["questions"]) == 2
        assert any(event["stage"] == "recovered" for event in completed["events"])
        assert state["supervisor"] == 0


def test_structural_validation_and_limits():
    with pytest.raises(ValueError):
        generation.AssessmentBlueprint(title="Bad marks", total_marks=7, questions=blueprint().questions)
    with pytest.raises(ValueError):
        generation.AssessmentBlueprint(title="Too many", total_marks=21,
            questions=[blueprint().questions[0].model_copy(update={"marks": 1})] * 21)
    source = [{"chunk_id": "real"}]
    mcq = generation.GeneratedQuestion(stem="Which of the following is a useful example?",
        options=["A", "B", "C", "D"], correct_answer="A",
        explanation="The correct example is A for the stated reason.", source_ids=["invented"])
    with pytest.raises(ValueError, match="source"):
        generation.validate_question(mcq, blueprint().questions[0], source)
    with pytest.raises(ValueError, match="four unique"):
        generation.validate_question(mcq.model_copy(update={"source_ids": [], "options": ["A", "A", "B", "C"]}),
                                      blueprint().questions[0], [])
