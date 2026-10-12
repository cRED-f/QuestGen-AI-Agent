"""Generation remains observable even when the model outlives the HTTP proxy."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service, generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'async-exam.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def prepared_exam(client, monkeypatch):
    course_id = client.get('/api/courses').json()[0]['id']
    thread_id = client.post('/api/threads', json={'course_id': course_id}).json()['id']
    root = f'/api/threads/{thread_id}/exam'
    payload = {'updates': {'faculty_initials': 'FI', 'university_name': 'University',
                           'exam_type': 'midterm', 'total_marks': 5, 'difficulty': 'moderate',
                           'question_formats': [{'kind': 'mcq', 'count': 1, 'marks_each': 5}],
                           'material_scope': 'general'}}
    assert client.patch(root + '/requirements', json=payload).status_code == 200
    assert client.post(root + '/approve').status_code == 200
    assert client.post(root + '/paper/prepare').status_code == 200
    assert client.post(root + '/paper/approve').status_code == 200
    monkeypatch.setattr(service, 'model_credentials', lambda settings: ('http://test/', {}))
    with service.database() as conn:
        conn.execute("UPDATE workspace_settings SET model='mock-model' WHERE id=1")
    return root + '/paper'


def await_status(client, base, request_id, expected, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(base + '/generation/' + request_id)
        assert response.status_code == 200, response.text
        result = response.json()
        if result['status'] == expected:
            return result
        time.sleep(0.02)
    pytest.fail(f'Generation did not become {expected}; latest status: {result}')


def test_generation_start_is_nonblocking_and_double_click_safe(client, monkeypatch):
    base = prepared_exam(client, monkeypatch)

    async def slow_generate(_settings, _plan, _sources, _feedback=''):
        await asyncio.sleep(0.4)
        return generation.GeneratedQuestion(
            stem='Which graph traversal uses a queue data structure?',
            options=['Breadth-first', 'Depth-first', 'Dijkstra', 'Prim'],
            correct_answer='Breadth-first',
            explanation='Breadth-first search traverses graph layers using a queue.',
            source_ids=[])

    monkeypatch.setattr(generation, 'generate_question', slow_generate)
    started_at = time.monotonic()
    start = client.post(base + '/generation/start', json={})
    elapsed = time.monotonic() - started_at
    assert start.status_code == 202, start.text
    assert elapsed < 0.2, 'Start must not await the provider response'
    request_id = start.json()['request_id']
    duplicate = client.post(base + '/generation/start', json={})
    assert duplicate.status_code == 409
    running = client.get(base + '/generation/' + request_id).json()
    assert running['status'] in ('queued', 'running')
    ready = await_status(client, base, request_id, 'succeeded')
    assert ready['paper']['candidate']['stem'].startswith('Which graph traversal')
    assert ready['paper']['questions'] == []
    assert client.get(base + '/generation/not-that-request').status_code == 404


def test_generation_failure_returns_readable_error_and_retry_works(client, monkeypatch):
    base = prepared_exam(client, monkeypatch)

    async def cannot_reach(_settings, _plan, _sources, _feedback=''):
        raise HTTPException(status_code=503, detail='The model provider is unavailable. Check Ollama.')

    monkeypatch.setattr(generation, 'generate_question', cannot_reach)
    request_id = client.post(base + '/generation/start', json={}).json()['request_id']
    result = await_status(client, base, request_id, 'failed')
    assert 'Check Ollama' in result['error']
    assert result['paper']['status'] == 'ready'
    assert result['paper']['candidate'] is None
    request_id = client.post(base + '/generation/start', json={}).json()['request_id']
    assert await_status(client, base, request_id, 'failed')['status'] == 'failed'


def test_cancellation_invalidates_generation_and_preserves_paper(client, monkeypatch):
    base = prepared_exam(client, monkeypatch)

    async def slow_generate(_settings, _plan, _sources, _feedback=''):
        await asyncio.sleep(3)
        raise AssertionError('The cancelled model must not write a new candidate')

    monkeypatch.setattr(generation, 'generate_question', slow_generate)
    request_id = client.post(base + '/generation/start', json={}).json()['request_id']
    await_status(client, base, request_id, 'running')
    cancelled = client.post(base + '/cancel')
    assert cancelled.status_code == 200, cancelled.text
    status = await_status(client, base, request_id, 'cancelled')
    assert status['paper']['candidate'] is None
    assert status['paper']['status'] == 'ready'
    assert client.post(base + '/generation/start', json={}).status_code == 202
    # The TestClient lifespan shuts down the second task without leaving a reservation.


def test_restart_marks_lost_generation_request_failed(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'restart.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        course_id = api.get('/api/courses').json()[0]['id']
        tid = api.post('/api/threads', json={'course_id': course_id}).json()['id']
        # Create a paper via approved requirements.
        root = f'/api/threads/{tid}/exam'
        payload = {'updates': {'faculty_initials': 'FI', 'university_name': 'University',
                               'exam_type': 'midterm', 'total_marks': 5, 'difficulty': 'moderate',
                               'question_formats': [{'kind': 'mcq', 'count': 1, 'marks_each': 5}],
                               'material_scope': 'general'}}
        assert api.patch(root + '/requirements', json=payload).status_code == 200
        assert api.post(root + '/approve').status_code == 200
        assert api.post(root + '/paper/prepare').status_code == 200
        assert api.post(root + '/paper/approve').status_code == 200
        with service.database() as conn:
            conn.execute('INSERT INTO exam_paper_generation_requests(id,thread_id,status,created_at,updated_at) '
                         "VALUES(? ,?,'running',0,0)", ('previous-request', tid))
            conn.execute("UPDATE exam_papers SET status='generating',generation_token='lost' WHERE thread_id=?", (tid,))
    with TestClient(service.app) as api:
        result = api.get(f'{root}/paper/generation/previous-request')
        assert result.status_code == 200
        assert result.json()['status'] == 'failed'
        assert 'restarted' in result.json()['error']
        assert result.json()['paper']['status'] == 'ready'


def test_run_isolated_to_its_thread_and_removed_on_delete(client, monkeypatch):
    base = prepared_exam(client, monkeypatch)
    other = prepared_exam(client, monkeypatch)

    async def slow_generate(*args, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError('Deleted thread generation should be cancelled')

    monkeypatch.setattr(generation, 'generate_question', slow_generate)
    started = client.post(base + '/generation/start', json={})
    request_id = started.json()['request_id']
    assert client.get(other + '/generation/' + request_id).status_code == 404
    thread_id = base.split('/')[3]
    assert client.delete('/api/threads/' + thread_id).status_code == 204
    assert client.get(base + '/generation/' + request_id).status_code == 404


def test_empty_provider_answer_is_retried_and_candidate_saved(client, monkeypatch):
    """An empty model answer must not permanently break the exam paper."""
    base = prepared_exam(client, monkeypatch)
    attempts = []

    async def model_reply(_settings, _system, _request, *, max_tokens=4096):
        attempts.append(max_tokens)
        if len(attempts) == 1:
            raise ValueError('Model exhausted its output token limit before returning JSON')
        return {
            'stem': 'Which graph search algorithm uses a FIFO queue?',
            'options': ['Breadth-first search', 'Depth-first search', 'Kruskal', 'Prim'],
            'correct_answer': 'Breadth-first search',
            'explanation': 'Breadth-first search uses FIFO processing of the frontier.',
            'source_ids': [],
        }

    monkeypatch.setattr(generation, 'model_json', model_reply)
    request = client.post(base + '/generation/start', json={})
    assert request.status_code == 202, request.text
    result = await_status(client, base, request.json()['request_id'], 'succeeded')
    assert result['paper']['candidate']['correct_answer'] == 'Breadth-first search'
    assert attempts == [4096, 8192]
