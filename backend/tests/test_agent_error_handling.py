"""Agent errors expose actionable categories without leaking provider internals."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as service
from backend.agent import supervisor
from backend.agent.errors import classify


def _http_error(status, body):
    request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(body, request=request, response=response)


@pytest.mark.parametrize('status,code', [
    (400, 'provider_rejected_request'),
    (401, 'provider_auth'),
    (404, 'model_not_found'),
    (429, 'rate_limited'),
    (503, 'provider_unavailable'),
])
def test_provider_failure_classification_never_leaks_secret(status, code):
    failure = classify(_http_error(status, 'sk-super-secret provider payload'), 'agent_execution')
    assert failure.code == code
    assert 'sk-super-secret' not in failure.guidance
    assert failure.stage == 'agent_execution'


def test_academic_parser_failure_is_actionable():
    failure = classify(ValueError('Academic analyst returned invalid JSON content'), 'topic_analysis')
    assert failure.code == 'invalid_topic_analysis'
    assert 'JSON' in failure.guidance


def test_truncated_analyst_json_raises_keyword_matchable_value_error():
    # Regression: a brace-bearing but invalid scan used to leak a raw
    # JSONDecodeError whose text lacks 'json', bypassing the soften path.
    from backend.agent.academic import parse_json
    with pytest.raises(ValueError, match='(?i)json'):
        parse_json('{"topics": [{"title": "Photosynthesis"} ,"desc": "truncat}')


def test_timeout_failure_is_not_mislabeled_as_provider_connection():
    failure = classify(asyncio.TimeoutError(), 'agent_execution')
    assert failure.status_code == 504
    assert failure.code == 'timeout'


def test_missing_dependency_has_specific_guidance():
    failure = classify(ModuleNotFoundError("No module named 'deepagents'"), 'agent_setup')
    assert failure.status_code == 503
    assert failure.code == 'missing_dependencies'


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'test.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        course = api.get('/api/courses').json()[0]['id']
        tid = api.post('/api/threads', json={'course_id': course}).json()['id']
        api.put('/api/settings', json={'provider': 'ollama', 'model': 'local-tool-model'})
        yield api, tid


def test_failing_agent_reports_stage_reference_and_preserves_session(client, monkeypatch):
    api, tid = client

    async def broken(*args, **kwargs):
        raise _http_error(400, 'sk-super-secret model rejected tool call')

    monkeypatch.setattr(supervisor, 'run_turn', broken)
    response = api.post(f'/api/threads/{tid}/agent/turn', json={'content': 'Investigate my topics'})
    assert response.status_code == 502
    assert 'tool/function calling' in response.json()['detail']
    assert 'reference:' in response.json()['detail']
    assert 'sk-super-secret' not in response.text
    assert response.headers.get('X-QuestGen-Error-Reference')
    assert api.get(f'/api/threads/{tid}/agent/status').json()['status'] == 'ready'
    assert api.get(f'/api/threads/{tid}/messages').json() == []


def test_timed_out_agent_returns_504(client, monkeypatch):
    api, tid = client

    async def broken(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(supervisor, 'run_turn', broken)
    response = api.post(f'/api/threads/{tid}/agent/turn', json={'content': 'Explore topics'})
    assert response.status_code == 504
    assert 'timed out' in response.json()['detail']


def test_pending_professor_question_survives_model_failure(client, monkeypatch):
    api, tid = client
    async def paused(*args, **kwargs):
        return 'Choose conceptual or numerical questions?', {'question': 'Choose conceptual or numerical questions?'}, []
    monkeypatch.setattr(supervisor, 'run_turn', paused)
    first = api.post(f'/api/threads/{tid}/agent/turn', json={'content': 'Investigate my course'})
    assert first.status_code == 200

    async def broken(*args, **kwargs):
        raise ConnectionError('local model unreachable')
    monkeypatch.setattr(supervisor, 'run_turn', broken)
    response = api.post(f'/api/threads/{tid}/agent/turn', json={'content': 'Numerical'})
    assert response.status_code == 503
    snapshot = api.get(f'/api/threads/{tid}/agent/status').json()
    assert snapshot['status'] == 'awaiting_user'
    assert 'Choose conceptual' in snapshot['pending_question']['question']
