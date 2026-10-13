"""Agent drafts stay in Agent Mode until an explicit selected-question handoff."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend import agent_mode, agent_consultation, app as service


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'agent-import.db'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        api.put('/api/settings', json={'provider': 'ollama', 'model': 'test'})
        yield api


def thread(client):
    course = client.get('/api/courses').json()[0]['id']
    return course, client.post('/api/threads', json={'course_id': course}).json()['id']


def question(kind='mcq', num=1):
    return {'kind': kind, 'stem': f'Question {num}: What is a data structure?',
            'options': [f'{num}. Stack', 'Queue', 'Heap', 'Tree'] if kind == 'mcq' else [],
            'correct_answer': 'Queue' if kind == 'mcq' else 'A way to organize data.',
            'explanation': 'This is the expected response.',
            'topic': f'Chapter {num}', 'marks': 2 if kind == 'mcq' else 5}


def approve_drafting(client, tid):
    with service.database() as conn:
        proposed = agent_consultation.propose(conn, tid, None, {
            'summary': 'Prepare questions about data structures and their use.',
            'details': [{'label': 'Formats', 'value': 'Mixed MCQ and short answer'}],
        })
    approved = client.post(f'/api/threads/{tid}/agent/plan/approve', json={'version': proposed['version']})
    assert approved.status_code == 200, approved.text


def save(client, course, tid):
    approve_drafting(client, tid)
    tools = agent_mode.Tools(course, tid, None, allow_generation=True)
    result = asyncio.run(tools.invoke('draft_questions', {'questions': [question('mcq'), question('short_answer', 2)]}))
    assert json.loads(result)['saved'] is True
    return client.get(f'/api/threads/{tid}/agent/questions').json()


def test_drafts_can_be_selected_edited_and_imported_in_exam_without_regeneration(client):
    course, tid = thread(client)
    # Conversation text, not a hypothetical summary, accompanies the handoff.
    user = client.post(f'/api/threads/{tid}/agent/stream', json={'content': 'Give me a mixed paper'})
    # Provider is intentionally offline; user request remains saved.
    assert user.status_code == 200
    drafts = save(client, course, tid)
    untouched = drafts[0]['content']
    first_id = drafts[0]['id']
    second_id = drafts[1]['id']
    selected = client.patch(f'/api/threads/{tid}/agent/questions/{second_id}', json={'selected': False,
                                                                                'version': drafts[1]['version']})
    assert selected.status_code == 200
    invalid = client.post(f'/api/threads/{tid}/exam/import-agent-questions', json={
        'question_ids': [first_id, second_id]})
    assert invalid.status_code == 409
    result = client.post(f'/api/threads/{tid}/exam/import-agent-questions', json={'question_ids': [first_id]})
    assert result.status_code == 200, result.text
    imported = result.json()
    assert imported['paper']['status'] == 'complete'
    assert imported['paper']['total_questions'] == 1
    assert imported['paper']['questions'][0]['content']['stem'] == untouched['stem']
    assert imported['paper']['questions'][0]['content']['correct_answer'] == untouched['correct_answer']
    assert imported['paper']['final_approved'] is False
    assert imported['session']['status'] == 'approved'
    assert imported['handoff']['question_ids'] == [first_id]
    assert any(item['content'] == 'Give me a mixed paper' for item in imported['handoff']['conversation'])
    # Selection is a deliberate, durable print/export gate.
    assert client.post(f'/api/threads/{tid}/exam/paper/final-approve').status_code == 200
    assert client.get(f'/api/threads/{tid}/exam/paper').json()['final_approved'] is True
    # Old paper cannot be replaced without explicit confirmation.
    refused = client.post(f'/api/threads/{tid}/exam/import-agent-questions', json={'question_ids': [first_id]})
    assert refused.status_code == 409


def test_import_only_takes_explicitly_selected_question_ids_and_rejects_cross_thread(client):
    course, a = thread(client)
    _, b = thread(client)
    drafts = save(client, course, a)
    wrong = client.post(f'/api/threads/{b}/exam/import-agent-questions', json={
        'question_ids': [drafts[0]['id']]})
    assert wrong.status_code == 409
    response = client.post(f'/api/threads/{a}/exam/import-agent-questions', json={
        'question_ids': [drafts[0]['id'], drafts[1]['id']]})
    assert response.status_code == 200, response.text
    result = response.json()['paper']
    assert len(result['blueprint']['sections']) == 2
    assert [q['content']['kind'] for q in result['questions']] == ['mcq', 'short_answer']
    assert result['total_marks'] == 7
    assert client.get(f'/api/threads/{a}/exam/agent-handoff').json()['question_ids'] == [q['id'] for q in drafts]


def test_edit_then_import_preserves_user_changes_and_question_versions(client):
    course, tid = thread(client)
    original = save(client, course, tid)[0]
    edited = {**original['content'], 'stem': 'Updated stem: which structure is FIFO?'}
    response = client.patch(f'/api/threads/{tid}/agent/questions/{original["id"]}',
                            json={'question': edited, 'version': original['version']})
    assert response.status_code == 200
    stale = client.patch(f'/api/threads/{tid}/agent/questions/{original["id"]}',
                         json={'selected': False, 'version': original['version']})
    assert stale.status_code == 409
    result = client.post(f'/api/threads/{tid}/exam/import-agent-questions', json={'question_ids': [original['id']]})
    assert result.status_code == 200, result.text
    assert result.json()['paper']['questions'][0]['content']['stem'] == edited['stem']
    with service.database() as db:
        assert db.execute('SELECT action FROM exam_paper_versions WHERE question_id=?',
                          (result.json()['paper']['questions'][0]['id'],)).fetchone()['action'] == 'imported_from_agent'


def test_invalid_questions_are_rejected_before_any_draft_is_saved(client):
    course, tid = thread(client)
    approve_drafting(client, tid)
    tools = agent_mode.Tools(course, tid, None, allow_generation=True)
    bad = asyncio.run(tools.invoke('draft_questions', {'questions': [question('mcq'),
        {**question('mcq', 2), 'options': ['A', 'B', 'C', 'D'], 'correct_answer': 'E'}]}))
    assert 'Could not save drafts' in bad
    assert client.get(f'/api/threads/{tid}/agent/questions').json() == []


def test_imported_layout_can_change_without_regenerating_or_bypassing_final_approval(client):
    course, tid = thread(client)
    draft_id = save(client, course, tid)[0]['id']
    response = client.post(f'/api/threads/{tid}/exam/import-agent-questions', json={'question_ids': [draft_id]})
    assert response.status_code == 200
    first = response.json()['paper']['questions'][0]
    assert client.post(f'/api/threads/{tid}/exam/paper/final-approve').status_code == 200
    changed = client.put(f'/api/threads/{tid}/exam/paper/layout', json={
        'title': 'Autumn Midterm', 'instructions': 'Answer each question in the space provided.'})
    assert changed.status_code == 200, changed.text
    assert changed.json()['blueprint']['title'] == 'Autumn Midterm'
    assert changed.json()['questions'][0]['id'] == first['id']
    assert changed.json()['questions'][0]['content'] == first['content']
    assert changed.json()['final_approved'] is False
    assert client.post(f'/api/threads/{tid}/exam/paper/final-approve').status_code == 200
    exported = client.get(f'/api/threads/{tid}/exam/paper/export/student.docx')
    assert exported.status_code == 200, exported.text[:100] if exported.status_code != 200 else ''
    assert len(exported.content) > 200


def test_model_tool_call_streams_and_persists_questions_in_agent_mode(client, monkeypatch):
    course, tid = thread(client)
    approve_drafting(client, tid)
    calls = 0

    async def fake_stream_provider(client_http, *, settings, url, headers, messages, tool_choice):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield 'reasoning', 'Drafting a mixed set of questions'
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall('q-1', 'draft_questions',
                json.dumps({'questions': [question('mcq', 3), question('short_answer', 4)]}))])
        else:
            yield 'delta', 'I drafted two questions. Select the ones you want to format.'
            yield 'result', agent_mode.ModelResult('I drafted two questions. Select the ones you want to format.')

    monkeypatch.setattr(agent_mode, 'stream_provider', fake_stream_provider)
    result = client.post(f'/api/threads/{tid}/agent/stream', json={
        'content': 'Draft two questions, an MCQ and short answer.', 'generate_from_plan': True})
    assert result.status_code == 200, result.text
    events = [json.loads(row[6:]) for row in result.text.splitlines() if row.startswith('data: ')]
    assert events[0]['type'] == 'started'
    assert any(event['type'] == 'reasoning' for event in events)
    assert any(event['type'] == 'activity' and event.get('name') == 'draft_questions' for event in events)
    assert events[-1]['type'] == 'done'
    assert calls == 2
    persisted = client.get(f'/api/threads/{tid}/agent/questions').json()
    assert len(persisted) == 2
    assert all(item['origin_message_id'] == events[0]['user_id'] for item in persisted)
    assert client.get(f'/api/threads/{tid}/exam').json()['status'] == 'not_started'


def test_regenerating_agent_answer_removes_superseded_questions(client, monkeypatch):
    _, tid = thread(client)
    approve_drafting(client, tid)
    turn = 0
    async def fake_provider(client_http, *, settings, url, headers, messages, tool_choice):
        nonlocal turn
        turn += 1
        if turn in (1, 3):
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall('draft', 'draft_questions',
                json.dumps({'questions': [question('mcq', turn)]}))])
        else:
            yield 'delta', 'Questions updated.'
            yield 'result', agent_mode.ModelResult('Questions updated.')
    monkeypatch.setattr(agent_mode, 'stream_provider', fake_provider)
    first = client.post(f'/api/threads/{tid}/agent/stream', json={'content': 'Create one MCQ', 'generate_from_plan': True})
    first_events = [json.loads(row[6:]) for row in first.text.splitlines() if row.startswith('data: ')]
    answer_id = first_events[-1]['message_id']
    old_draft = client.get(f'/api/threads/{tid}/agent/questions').json()[0]['id']
    replay = client.post(f'/api/threads/{tid}/agent/stream', json={'regenerate_message_id': answer_id})
    replay_events = [json.loads(row[6:]) for row in replay.text.splitlines() if row.startswith('data: ')]
    assert replay_events[-1]['type'] == 'done'
    new_drafts = client.get(f'/api/threads/{tid}/agent/questions').json()
    assert len(new_drafts) == 1
    assert new_drafts[0]['id'] != old_draft
