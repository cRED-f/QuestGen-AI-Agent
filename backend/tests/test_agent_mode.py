"""Regression tests for the rebuilt Agent Mode SSE + bounded autonomous tools."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import agent_mode, agent_consultation, app as service


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "agent-mode.sqlite3"))
    monkeypatch.setenv("QUESTGEN_UPLOAD_DIR", str(tmp_path / "uploads"))
    with TestClient(service.app) as api:
        api.put('/api/settings', json={"provider": "ollama", "model": "agent-test"})
        yield api


def create_thread(client):
    course_id = client.get('/api/courses').json()[0]['id']
    thread_id = client.post('/api/threads', json={"course_id": course_id}).json()['id']
    return course_id, thread_id


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def await_document_ready(client, course_id, document_id):
    for _ in range(200):
        response = client.get(f'/api/courses/{course_id}/documents/{document_id}')
        state = response.json()
        if state['status'] == 'ready':
            return
        if state['status'] == 'failed':
            pytest.fail(f"Test document could not be indexed: {state.get('error')}")
        time.sleep(0.01)
    pytest.fail('Test document indexing timed out')


def test_agent_stream_begins_immediately_and_saves_completed_answer(client, monkeypatch):
    _, thread_id = create_thread(client)
    observed = []

    async def fake_agent(settings, url, headers, history, content, tools):
        observed.append((history, content))
        yield {"type": "status", "message": "Connecting to model"}
        yield {"type": "reasoning", "content": "Checking details"}
        yield {"type": "activity", "name": "list_course_documents", "stage": "started"}
        yield {"type": "activity", "name": "list_course_documents", "stage": "completed"}
        yield {"type": "delta", "content": "Here is "}
        yield {"type": "delta", "content": "the result"}
        yield {"type": "final", "content": "Here is the result"}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    response = client.post(f'/api/threads/{thread_id}/agent/stream', json={"content": "Investigate this"})
    assert response.status_code == 200
    stream = events(response)
    assert [item['type'] for item in stream] == [
        'started', 'status', 'status', 'reasoning', 'activity', 'activity', 'delta', 'delta', 'done'
    ]
    assert stream[-1]['message_id']
    assert observed == [([], 'Investigate this')]
    saved = client.get(f'/api/threads/{thread_id}/messages').json()
    assert [(m['mode'], m['role'], m['content']) for m in saved] == [
        ('agent', 'user', 'Investigate this'), ('agent', 'assistant', 'Here is the result')
    ]


def test_failing_model_stays_inside_sse_and_does_not_save_fake_answer(client, monkeypatch):
    _, thread_id = create_thread(client)

    async def unavailable(*args):
        raise RuntimeError('Model provider returned HTTP 502')
        yield {}

    monkeypatch.setattr(agent_mode, 'run_agent', unavailable)
    response = client.post(f'/api/threads/{thread_id}/agent/stream', json={"content": "First question"})
    stream = events(response)
    assert stream[0]['type'] == 'started'
    assert stream[-1] == {'type': 'error', 'message': 'Model provider returned HTTP 502'}
    saved = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(saved) == 1 and saved[0]['role'] == 'user'


def test_agent_input_rejects_unknown_course_document_without_persisting(client):
    _, thread_id = create_thread(client)
    res = client.post(f'/api/threads/{thread_id}/agent/stream', json={
        'content': 'Read this file', 'document_ids': ['other-course-file']})
    assert res.status_code == 422
    assert client.get(f'/api/threads/{thread_id}/messages').json() == []


@pytest.mark.asyncio
async def test_agent_tool_budget_and_dynamic_loop(monkeypatch):
    tools = agent_mode.Tools('course-id', 'thread-id', None)
    step = 0
    called = []

    async def fake_stream(client, *, settings, url, headers, messages, tool_choice):
        nonlocal step
        step += 1
        called.append((tool_choice, messages[-1]['role']))
        if step == 1:
            yield 'reasoning', 'Need source'
            yield 'result', agent_mode.ModelResult('', [agent_mode.ToolCall('one', 'search_web', '{"query":"test"}')])
        else:
            yield 'delta', 'Answer [W1]'
            yield 'result', agent_mode.ModelResult('Answer [W1]')

    async def fake_tool(name, args):
        assert name == 'search_web'
        tools.web_sources.append({'reference': 'W1', 'title': 'Source', 'url': 'https://example.org'})
        return '{"pages": [{"reference": "W1", "excerpt": "evidence"}]}'

    monkeypatch.setattr(agent_mode, 'stream_provider', fake_stream)
    monkeypatch.setattr(tools, 'invoke', fake_tool)
    output = [event async for event in agent_mode.run_agent(
        {'provider': 'ollama', 'model': 'm'}, 'https://example.org/api', {}, [], 'Find it', tools)]
    assert [event['type'] for event in output] == [
        'status', 'reasoning', 'activity', 'activity', 'sources', 'status', 'delta', 'final'
    ]
    assert called == [('auto', 'user'), ('auto', 'tool')]
    assert output[-1]['content'] == 'Answer [W1]'
    assert output[4]['web_sources'][0]['url'] == 'https://example.org'


@pytest.mark.asyncio
async def test_search_web_hard_budget_even_if_model_requests_more(monkeypatch):
    tools = agent_mode.Tools('course-id', 'thread-id', None)
    requested = []

    async def fake_search(client, query):
        requested.append(query)
        return []

    monkeypatch.setattr(agent_mode, '_search', fake_search)
    # No external page fetch is made for an empty search response.
    await tools.invoke('search_web', {'query': 'research number one'})
    await tools.invoke('search_web', {'query': 'research number two'})
    exhausted = await tools.invoke('search_web', {'query': 'research number three'})
    assert requested == ['research number one', 'research number two']
    assert 'budget exhausted' in exhausted


@pytest.mark.asyncio
async def test_public_url_blocks_private_and_local_targets(monkeypatch):
    def fake_dns(*args, **kwargs):
        return [(2, 1, 6, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(agent_mode.socket, 'getaddrinfo', fake_dns)
    assert await agent_mode._public_url('http://example.com') is None
    assert await agent_mode._public_url('https://127.0.0.1/') is None
    assert await agent_mode._public_url('https://internal.local/') is None
    assert await agent_mode._public_url('https://example.org/private') is None


def test_agent_drafts_questions_without_exam_setup_or_approval(client):
    import asyncio
    _, thread_id = create_thread(client)
    tools = agent_mode.Tools(client.get(f'/api/threads/{thread_id}/exam').json()['course_id'], thread_id, None)
    blocked = asyncio.run(tools.invoke('draft_questions', {'questions': []}))
    assert 'Drafting blocked' in blocked
    with service.database() as conn:
        proposed = agent_consultation.propose(conn, thread_id, None, {'summary': 'A short assessment on core data structures', 'details': []})
    assert client.post(f'/api/threads/{thread_id}/agent/plan/approve', json={'version': proposed['version']}).status_code == 200
    tools.allow_generation = True  # explicit approved user action
    result = json.loads(asyncio.run(tools.invoke('draft_questions', {'questions': [{
        'kind': 'mcq', 'stem': 'Which data structure is FIFO?',
        'options': ['Stack', 'Queue', 'Tree', 'Graph'], 'correct_answer': 'Queue',
        'explanation': 'A queue follows first in, first out.', 'topic': 'Data structures'}]})))
    assert result['saved'] is True
    questions = client.get(f'/api/threads/{thread_id}/agent/questions').json()
    assert len(questions) == 1 and questions[0]['selected'] is True
    # Agent Mode produces the draft; Exam Mode is not invoked until handoff.
    assert client.get(f'/api/threads/{thread_id}/exam').json()['status'] == 'not_started'
    assert client.get(f'/api/threads/{thread_id}/exam/paper').json() is None


@pytest.mark.asyncio
async def test_openai_compatible_stream_parses_real_incremental_tool_calls():
    import httpx
    received = []
    packets = [
        {'choices': [{'delta': {'reasoning_content': 'Checking'}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'tool-a',
            'function': {'name': 'search_', 'arguments': '{"query":'}}]}}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0,
            'function': {'name': 'web', 'arguments': '"research"}'}}]}}]},
    ]

    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, text=''.join('data: '+json.dumps(item)+'\n\n' for item in packets)+'data: [DONE]\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as api:
        chunks = [item async for item in agent_mode.stream_provider(
            api, settings={'provider': 'ollama', 'model': 'test'},
            url='http://localhost/v1/chat/completions', headers={},
            messages=[{'role': 'system', 'content': agent_mode.SYSTEM}, {'role': 'user', 'content': 'Research'}])]
    assert received[0]['tool_choice'] == 'auto'
    assert chunks[0] == ('reasoning', 'Checking')
    assert chunks[-1][1].tool_calls == [agent_mode.ToolCall('tool-a', 'search_web', '{"query":"research"}')]


@pytest.mark.asyncio
async def test_web_fetches_no_more_than_three_sites_even_on_failure(monkeypatch):
    import httpx
    tools = agent_mode.Tools('c1', 't1', None)
    requested = []

    async def fake_search(client, query):
        return [{'title': 'Source', 'url': f'https://source{i}.org/article'} for i in range(6)]

    async def fake_public(url):
        return url

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(agent_mode, '_search', fake_search)
    monkeypatch.setattr(agent_mode, '_public_url', fake_public)
    monkeypatch.setattr(agent_mode.httpx, 'AsyncClient', offline_client)
    message = await tools.invoke('search_web', {'query': 'check three sources'})
    assert 'No readable' in message
    assert len(requested) == 3
    assert tools.web_pages == 3
    assert 'budget exhausted' in await tools.invoke('search_web', {'query': 'search again'})


def test_edit_older_agent_prompt_rebuilds_context_and_removes_later_turns(client, monkeypatch):
    _, thread_id = create_thread(client)
    called = []

    async def fake_agent(settings, url, headers, history, content, tools):
        called.append((history, content))
        tools.web_sources.append({"reference": "W1", "title": "Example", "url": "https://example.org/article"})
        yield {'type': 'delta', 'content': 'Response ' + str(len(called))}
        yield {'type': 'final', 'content': 'Response ' + str(len(called))}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    endpoint = f'/api/threads/{thread_id}/agent/stream'
    for content in ('Original prompt', 'Follow-up prompt'):
        response = client.post(endpoint, json={'content': content})
        assert response.status_code == 200 and events(response)[-1]['type'] == 'done'
    before = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(before) == 4
    assert before[1]['web_sources'][0]['reference'] == 'W1'

    response = client.post(endpoint, json={
        'edit_message_id': before[0]['id'], 'content': 'Corrected prompt'})
    assert response.status_code == 200
    stream = events(response)
    assert stream[0] == {'type': 'started', 'user_id': before[0]['id']}
    assert stream[-1]['type'] == 'done'
    after = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(after) == 2
    assert after[0]['id'] == before[0]['id']
    assert after[0]['content'] == 'Corrected prompt'
    assert after[1]['id'] != before[1]['id']
    assert [row['content'] for row in after] == ['Corrected prompt', 'Response 3']
    assert called[-1] == ([], 'Corrected prompt')
    with service.database() as conn:
        assert not conn.execute('SELECT 1 FROM messages WHERE id=?', (before[3]['id'],)).fetchone()
        assert not conn.execute('SELECT 1 FROM agent_web_sources WHERE message_id=?',
                                (before[1]['id'],)).fetchone()


def test_regenerate_agent_reply_reuses_prompt_history_and_selection(client, monkeypatch):
    course_id, thread_id = create_thread(client)
    upload = client.post(f'/api/courses/{course_id}/documents',
                         files={'file': ('chapter.txt', b'Knowledge of geometric proofs and formal logic.')})
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()['id']
    await_document_ready(client, course_id, doc_id)
    seen = []

    async def fake_agent(settings, url, headers, history, content, tools):
        seen.append((history, content, tools.selected_ids, tools.web_queries, tools.web_pages))
        yield {'type': 'final', 'content': f'Result {len(seen)}'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    endpoint = f'/api/threads/{thread_id}/agent/stream'
    assert events(client.post(endpoint, json={'content': 'Use chapter', 'document_ids': [doc_id]}))[-1]['type'] == 'done'
    assert events(client.post(endpoint, json={'content': 'Another thought'}))[-1]['type'] == 'done'
    before = client.get(f'/api/threads/{thread_id}/messages').json()
    first_reply = before[1]['id']
    replay = client.post(endpoint, json={'regenerate_message_id': first_reply})
    assert replay.status_code == 200
    assert events(replay)[0]['user_id'] == before[0]['id']
    assert events(replay)[-1]['type'] == 'done'
    after = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(after) == 2
    assert after[0]['id'] == before[0]['id']
    assert after[1]['id'] != first_reply
    assert seen[-1] == ([], 'Use chapter', [doc_id], 0, 0)
    assert [doc['id'] for doc in after[0]['documents']] == [doc_id]

    # Replay the latest reply too, leaving the original user turn untouched.
    current_reply = after[1]['id']
    assert events(client.post(endpoint, json={'regenerate_message_id': current_reply}))[-1]['type'] == 'done'
    latest = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(latest) == 2 and latest[0]['id'] == after[0]['id']
    assert latest[1]['id'] != current_reply
    assert seen[-1][2] == [doc_id]


def test_agent_edit_updates_selected_files_and_keeps_those_on_next_regeneration(client, monkeypatch):
    course_id, thread_id = create_thread(client)
    ids = []
    for name in ('one.txt', 'two.txt'):
        upload = client.post(f'/api/courses/{course_id}/documents',
                             files={'file': (name, b'This is a simple course document for research.')})
        assert upload.status_code == 201
        ids.append(upload.json()['id'])
        await_document_ready(client, course_id, ids[-1])
    seen = []

    async def fake_agent(settings, url, headers, history, content, tools):
        seen.append(tools.selected_ids)
        yield {'type': 'final', 'content': 'OK'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    path = f'/api/threads/{thread_id}/agent/stream'
    assert events(client.post(path, json={'content': 'Initial', 'document_ids': ids[:1]}))[-1]['type'] == 'done'
    original = client.get(f'/api/threads/{thread_id}/messages').json()
    assert events(client.post(path, json={'edit_message_id': original[0]['id'],
                                         'content': 'Updated', 'document_ids': ids[1:]}))[-1]['type'] == 'done'
    revised = client.get(f'/api/threads/{thread_id}/messages').json()
    assert [item['id'] for item in revised[0]['documents']] == ids[1:]
    assert events(client.post(path, json={'regenerate_message_id': revised[1]['id']}))[-1]['type'] == 'done'
    assert seen == [ids[:1], ids[1:], ids[1:]]


def test_invalid_agent_actions_and_cross_mode_targets_do_not_change_history(client, monkeypatch):
    _, thread_id = create_thread(client)
    _, other_thread = create_thread(client)

    async def fake_agent(settings, url, headers, history, content, tools):
        yield {'type': 'final', 'content': 'Agent response'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    path = f'/api/threads/{thread_id}/agent/stream'
    assert events(client.post(path, json={'content': 'Agent user'}))[-1]['type'] == 'done'
    own = client.get(f'/api/threads/{thread_id}/messages').json()
    with service.database() as conn:
        for mode in ('chat', 'exam'):
            conn.execute("INSERT INTO messages(id,thread_id,role,content,created_at,mode) "
                         "VALUES (?,?,'user',?,?,?)", (service.identifier(), thread_id,
                                                        mode, service.now(), mode))
    before = client.get(f'/api/threads/{thread_id}/messages').json()
    errors = (
        ({'edit_message_id': own[1]['id'], 'content': 'Wrong role'}, 404),
        ({'regenerate_message_id': own[0]['id']}, 404),
        ({'edit_message_id': before[2]['id'], 'content': 'Wrong mode'}, 404),
        ({'edit_message_id': before[3]['id'], 'content': 'Wrong mode'}, 404),
        ({'edit_message_id': 'not-present', 'content': 'Missing'}, 404),
        ({'regenerate_message_id': 'not-present'}, 404),
        ({'edit_message_id': own[0]['id'], 'content': ''}, 422),
        ({'regenerate_message_id': own[1]['id'], 'content': 'No override'}, 422),
        ({'regenerate_message_id': own[1]['id'], 'edit_message_id': own[0]['id']}, 422),
    )
    for request, expected in errors:
        response = client.post(path, json=request)
        assert response.status_code == expected, response.text
        assert client.get(f'/api/threads/{thread_id}/messages').json() == before
    foreign = client.post(f'/api/threads/{other_thread}/agent/stream',
                          json={'edit_message_id': own[0]['id'], 'content': 'Steal'})
    assert foreign.status_code == 404
    assert client.get(f'/api/threads/{other_thread}/messages').json() == []


def test_agent_provider_preflight_rejects_edit_without_deleting_existing_turns(client, monkeypatch):
    _, thread_id = create_thread(client)

    async def fake_agent(settings, url, headers, history, content, tools):
        yield {'type': 'final', 'content': 'Initial answer'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    path = f'/api/threads/{thread_id}/agent/stream'
    assert events(client.post(path, json={'content': 'Initial request'}))[-1]['type'] == 'done'
    before = client.get(f'/api/threads/{thread_id}/messages').json()

    def missing_credentials(settings):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail='No configured provider')

    monkeypatch.setattr(service, 'model_credentials', missing_credentials)
    for req in ({'edit_message_id': before[0]['id'], 'content': 'Revision'},
                {'regenerate_message_id': before[1]['id']}):
        response = client.post(path, json=req)
        assert response.status_code == 422
        assert client.get(f'/api/threads/{thread_id}/messages').json() == before


def test_regenerate_latest_agent_response_preserves_preceding_context(client, monkeypatch):
    _, thread_id = create_thread(client)
    histories = []

    async def fake_agent(settings, url, headers, history, content, tools):
        histories.append((history, content))
        yield {'type': 'final', 'content': 'Reply ' + str(len(histories))}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    path = f'/api/threads/{thread_id}/agent/stream'
    for prompt in ('First', 'Second'):
        assert events(client.post(path, json={'content': prompt}))[-1]['type'] == 'done'
    original = client.get(f'/api/threads/{thread_id}/messages').json()
    response = client.post(path, json={'regenerate_message_id': original[3]['id']})
    assert response.status_code == 200 and events(response)[-1]['type'] == 'done'
    updated = client.get(f'/api/threads/{thread_id}/messages').json()
    assert len(updated) == 4
    assert [item['id'] for item in updated[:3]] == [item['id'] for item in original[:3]]
    assert updated[3]['id'] != original[3]['id']
    assert histories[-1] == ([{'role': 'user', 'content': 'First'},
                             {'role': 'assistant', 'content': 'Reply 1'}], 'Second')


def test_agent_thread_busy_rejects_edit_and_regenerate_without_changes(client, monkeypatch):
    _, thread_id = create_thread(client)

    async def fake_agent(settings, url, headers, history, content, tools):
        yield {'type': 'final', 'content': 'Reply'}

    monkeypatch.setattr(agent_mode, 'run_agent', fake_agent)
    endpoint = f'/api/threads/{thread_id}/agent/stream'
    assert events(client.post(endpoint, json={'content': 'Prompt'}))[-1]['type'] == 'done'
    before = client.get(f'/api/threads/{thread_id}/messages').json()
    service.app.state.active_agent_threads.add(thread_id)
    try:
        for body in ({'edit_message_id': before[0]['id'], 'content': 'Changed'},
                     {'regenerate_message_id': before[1]['id']}):
            assert client.post(endpoint, json=body).status_code == 409
            assert client.get(f'/api/threads/{thread_id}/messages').json() == before
    finally:
        service.app.state.active_agent_threads.discard(thread_id)
