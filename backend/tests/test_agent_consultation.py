"""Agent consultation must precede explicit, bounded-scope draft authorization."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import agent_consultation, agent_mode, app as service


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'consultation.sqlite'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as client:
        client.put('/api/settings', json={'provider': 'ollama', 'model': 'mock'})
        course = client.get('/api/courses').json()[0]['id']
        tid = client.post('/api/threads', json={'course_id': course}).json()['id']
        yield client, course, tid


def stream_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def question():
    return {'kind': 'short_answer', 'stem': 'Explain how binary search works.',
            'correct_answer': 'Repeatedly halve sorted search space.',
            'explanation': 'Works on sorted sequences.', 'topic': 'Algorithms', 'marks': 4}


def propose_direct(tid, origin=None):
    with service.database() as conn:
        return agent_consultation.propose(conn, tid, origin, {
            'summary': 'Create a short assessment on algorithms with appropriate reasoning questions.',
            'details': [{'label': 'Coverage', 'value': 'Searching and sorting'},
                        {'label': 'Format', 'value': 'Short-answer and problem-solving'}]})


def test_consultation_first_pending_plan_requires_real_user_approval(setup, monkeypatch):
    client, course, tid = setup
    tool = agent_mode.Tools(course, tid, None)

    async def fake_provider(client_http, *, settings, url, headers, messages, tool_choice):
        if messages[-1]['role'] == 'user' and 'First' in messages[-1]['content']:
            # Even a badly behaving model cannot propose on its very first reply.
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall('p', 'propose_question_plan',
                json.dumps({'summary': 'A structured assessment on algorithms', 'details': []}))])
        elif messages[-1]['role'] == 'tool' and 'Consultation first' in messages[-1]['content']:
            yield 'delta', 'Which algorithm topics and question formats matter most?'
            yield 'result', agent_mode.ModelResult('Which algorithm topics and question formats matter most?')
        elif messages[-1]['role'] == 'user':
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall('p2', 'propose_question_plan',
                json.dumps({'summary': 'Assess searching and sorting with short-answer questions.',
                            'details': [{'label': 'Topics', 'value': 'Searching and sorting'}]}))])
        else:
            yield 'delta', 'Review the plan and approve when ready.'
            yield 'result', agent_mode.ModelResult('Review the plan and approve when ready.')

    monkeypatch.setattr(agent_mode, 'stream_provider', fake_provider)
    initial = stream_events(client.post(f'/api/threads/{tid}/agent/stream', json={'content': 'First, make an exam'}))
    assert initial[-1]['type'] == 'done'
    assert not any(event['type'] == 'plan' for event in initial)
    assert client.get(f'/api/threads/{tid}/agent/plan').json() is None

    followed = stream_events(client.post(f'/api/threads/{tid}/agent/stream', json={
        'content': 'Focus on search and sorting, short answers.'}))
    plans = [event['plan'] for event in followed if event['type'] == 'plan']
    assert len(plans) == 1 and plans[0]['status'] == 'pending'
    assert plans[0]['plan']['details'][0]['value'] == 'Searching and sorting'
    assert 'Drafting blocked' in await_invoke(tool, 'draft_questions', {'questions': [question()]})
    forbidden = client.post(f'/api/threads/{tid}/agent/stream', json={
        'content': 'Generate', 'generate_from_plan': True})
    assert forbidden.status_code == 409
    stale = client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': 991})
    assert stale.status_code == 409
    approval = client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': plans[0]['version']})
    assert approval.status_code == 200
    assert approval.json()['status'] == 'approved'
    # Approval by itself is insufficient: only the explicit draft action gets a grant.
    assert 'Drafting blocked' in await_invoke(tool, 'draft_questions', {'questions': [question()]})
    tool.allow_generation = True
    assert json.loads(await_invoke(tool, 'draft_questions', {'questions': [question()]}))['saved']
    assert len(client.get(f'/api/threads/{tid}/agent/questions').json()) == 1


def await_invoke(tool, name, args):
    return asyncio.run(tool.invoke(name, args))


def test_proposing_changed_plan_revokes_approval_and_generation_grant(setup):
    client, course, tid = setup
    first = propose_direct(tid)
    assert client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': first['version']}).status_code == 200
    tool = agent_mode.Tools(course, tid, None, allow_generation=True)
    assert json.loads(await_invoke(tool, 'draft_questions', {'questions': [question()]}))['saved']
    revised = propose_direct(tid)
    assert revised['version'] == first['version'] + 1
    assert revised['status'] == 'pending'
    assert 'Drafting blocked' in await_invoke(tool, 'draft_questions', {'questions': [question()]})
    assert client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': first['version']}).status_code == 409


def test_older_message_edit_invalidates_plan_from_later_turn(setup, monkeypatch):
    client, _, tid = setup

    async def fake_agent(settings, url, headers, history, content, tools):
        yield {'type': 'delta', 'content': 'Tell me more.'}
        yield {'type': 'final', 'content': 'Tell me more.'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    endpoint = f'/api/threads/{tid}/agent/stream'
    client.post(endpoint, json={'content': 'Help me create a quiz'})
    client.post(endpoint, json={'content': 'Cover sorting algorithms'})
    previous = client.get(f'/api/threads/{tid}/messages').json()
    second_user_id = previous[2]['id']
    propose_direct(tid, second_user_id)
    assert client.get(f'/api/threads/{tid}/agent/plan').json()
    edited = client.post(endpoint, json={'content': 'Help me study instead', 'edit_message_id': previous[0]['id']})
    assert stream_events(edited)[-1]['type'] == 'done'
    assert client.get(f'/api/threads/{tid}/agent/plan').json() is None


@pytest.mark.asyncio
async def test_agent_can_use_over_twelve_model_tool_cycles_without_app_step_limit(setup, monkeypatch):
    _, course, tid = setup
    tool = agent_mode.Tools(course, tid, None)
    calls = 0
    tool_calls = 0

    async def fake_stream(client_http, *, settings, url, headers, messages, tool_choice):
        nonlocal calls
        calls += 1
        assert tool_choice == 'auto'
        if calls <= 13:
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall(f'call-{calls}', 'list_course_documents', '{}')])
        else:
            yield 'delta', 'Done after research.'
            yield 'result', agent_mode.ModelResult('Done after research.')

    async def fake_invoke(name, args):
        nonlocal tool_calls
        tool_calls += 1
        return json.dumps({'files': []})

    monkeypatch.setattr(agent_mode, 'stream_provider', fake_stream)
    monkeypatch.setattr(tool, 'invoke', fake_invoke)
    response = [item async for item in agent_mode.run_agent({'provider': 'ollama', 'model': 'test'},
        'http://127.0.0.1:11434/v1/chat/completions', {}, [], 'Analyze thoroughly', tool)]
    assert calls == 14 and tool_calls == 13
    assert response[-1] == {'type': 'final', 'content': 'Done after research.'}
    assert sum(item['type'] == 'activity' and item.get('stage') == 'completed' for item in response) == 13


def test_generate_grant_is_recorded_and_restored_only_for_regeneration(setup, monkeypatch):
    client, _, tid = setup
    plan = propose_direct(tid)
    assert client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': plan['version']}).status_code == 200
    seen = []

    async def fake_agent(settings, url, headers, history, content, tools):
        seen.append(tools.allow_generation)
        yield {'type': 'delta', 'content': 'Draft handled.'}
        yield {'type': 'final', 'content': 'Draft handled.'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    endpoint = f'/api/threads/{tid}/agent/stream'
    first = stream_events(client.post(endpoint, json={'content': 'Generate from approved plan', 'generate_from_plan': True}))
    answer_id = first[-1]['message_id']
    assert seen == [True]
    stream_events(client.post(endpoint, json={'regenerate_message_id': answer_id}))
    assert seen == [True, True]
    stream_events(client.post(endpoint, json={'content': 'Explain the algorithm instead'}))
    assert seen[-1] is False
