"""API and storage contracts for the checkpointed academic consultation agent.

These tests stub the provider boundary, never invent a model response as evidence
that a live language model works. A full checkpoint integration test also needs
Deep Agents and an available model provider.
"""
import pytest
from fastapi.testclient import TestClient
from backend import app as service, course_tools
from backend.agent import supervisor, state as agent_state


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'agent.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def setup(client):
    course = client.get('/api/courses').json()[0]['id']
    thread = client.post('/api/threads', json={'course_id': course}).json()['id']
    client.put('/api/settings', json={'provider': 'ollama', 'model': 'local-test'})
    return course, thread


def test_actual_question_suspends_and_reply_resumes(client, monkeypatch):
    _, thread = setup(client)
    invoked = []

    async def fake_run(tid, cid, ids, settings, content, *, resume):
        invoked.append((tid, content, resume, ids))
        if not resume:
            return 'Would you assess algorithmic complexity or practical performance?', {
                'question': 'Would you assess algorithmic complexity or practical performance?',
                'context': 'Both themes appear relevant to your goal.'}, [{'tool': 'inspect_topic_coverage', 'detail': 'Completed'}]
        return 'I will prioritize practical performance and propose a course-grounded plan.', None, []

    monkeypatch.setattr(supervisor, 'run_turn', fake_run)
    path = f'/api/threads/{thread}/agent'
    assert client.get(path + '/status').json()['status'] == 'ready'
    result = client.post(path + '/turn', json={'content': 'Explore algorithms'}).json()
    assert result['reply'].startswith('Would you assess')
    assert result['state']['status'] == 'awaiting_user'
    assert result['state']['activity'][0]['tool'] == 'inspect_topic_coverage'
    service.init_db()  # persisted status is still present after schema reload
    assert client.get(path + '/status').json()['pending_question']['question'].startswith('Would you')
    second = client.post(path + '/turn', json={'content': 'Practical performance'}).json()
    assert second['state']['status'] == 'ready'
    assert [item[2] for item in invoked] == [False, True]
    saved = client.get(f'/api/threads/{thread}/messages').json()
    assert [item['mode'] for item in saved] == ['agent'] * 4
    assert saved[-1]['content'].startswith('I will prioritize')


def test_document_allowlist_cannot_change_mid_consultation(client, monkeypatch):
    course, thread = setup(client)
    with service.database() as conn:
        for doc in ('one', 'two'):
            conn.execute("""INSERT INTO documents
                (id,course_id,filename,extension,bytes,sha256,status,created_at,updated_at)
                VALUES (?,?,?,'.txt',1,?,'ready',?,?)""",
                (doc,course,doc+'.txt',doc,service.now(),service.now()))
    seen = []

    async def fake_run(tid, cid, ids, settings, content, *, resume):
        seen.append(ids)
        return 'Please choose a topic', {'question': 'Please choose a topic', 'context': ''}, []

    monkeypatch.setattr(supervisor, 'run_turn', fake_run)
    path = f'/api/threads/{thread}/agent/turn'
    assert client.post(path, json={'content': 'Begin', 'document_ids': ['one']}).status_code == 200
    assert client.post(path, json={'content': 'Now use another', 'document_ids': ['two']}).status_code == 409
    assert client.post(path, json={'content': 'Include another course file', 'document_ids': ['bad']}).status_code == 422
    assert client.post(path, json={'content': 'Topic A'}).status_code == 200
    assert seen == [['one'], ['one']]
    assert [x['id'] for x in course_tools.list_course_documents(course, ['one'])] == ['one']
    assert course_tools.read_document_chunks(course, 'two', document_ids=['one']) == []


def test_model_is_required_and_failed_run_does_not_save_chat(client, monkeypatch):
    _, thread = setup(client)
    client.put('/api/settings', json={'provider': 'ollama', 'model': ''})
    path = f'/api/threads/{thread}/agent'
    assert client.post(path+'/turn', json={'content': 'Hello'}).status_code == 422
    client.put('/api/settings', json={'provider': 'ollama', 'model': 'model'})

    async def fail(*args, **kwargs):
        raise RuntimeError('simulated model outage')

    monkeypatch.setattr(supervisor, 'run_turn', fail)
    assert client.post(path+'/turn', json={'content': 'Investigate'}).status_code == 502
    assert client.get(path+'/status').json()['status'] == 'ready'
    assert client.get(f'/api/threads/{thread}/messages').json() == []


def test_session_claim_prevents_concurrent_calls_and_remembers_scope(client):
    _, thread = setup(client)
    with service.database() as conn:
        previous, resume = agent_state.claim(conn, thread, [])
        assert not resume
    with service.database() as conn:
        with pytest.raises(ValueError, match='already running'):
            agent_state.claim(conn, thread, [])
        agent_state.finish(conn, thread, question={'question': 'Topic?', 'context': ''}, activity=[])
    with service.database() as conn:
        previous, resume = agent_state.claim(conn, thread, [])
        assert resume
        agent_state.fail(conn, thread, previous)
    assert client.get(f'/api/threads/{thread}/agent/status').json()['status'] == 'awaiting_user'


def test_ending_conversation_deletes_its_agent_state(client, monkeypatch):
    _, thread = setup(client)
    async def fake_run(*a, **kw):
        return 'Which topic should I explore?', {'question': 'Which topic should I explore?', 'context': ''}, []
    monkeypatch.setattr(supervisor, 'run_turn', fake_run)
    assert client.post(f'/api/threads/{thread}/agent/turn', json={'content': 'Explore'}).status_code == 200
    assert client.delete(f'/api/threads/{thread}').status_code == 204
    assert client.get(f'/api/threads/{thread}/agent/status').status_code == 404


def test_summarize_surfaces_interrupt_not_private_tool_output():
    class Interrupt:
        value = {'type': 'professor_question', 'question': 'Which topic matters most?',
                 'context': 'Choice changes the investigation'}
    class ToolMessage:
        type = 'tool'
        name = 'inspect_topic_coverage'
        content = 'private untrusted excerpt'
    output, pending, activities = supervisor.summarize({'__interrupt__': [Interrupt()], 'messages': [ToolMessage()]})
    assert output == 'Which topic matters most?'
    assert pending['context'] == 'Choice changes the investigation'
    assert activities == [{'tool': 'inspect_topic_coverage', 'detail': 'Completed'}]
    assert 'private' not in str((output, activities))


def test_graph_entrypoint_reuses_thread_and_resumes_command(client, monkeypatch):
    """Verify request wiring with a simulated saver/graph; not a live provider test."""
    import asyncio
    import sys
    import types
    from types import SimpleNamespace
    _, thread = setup(client)
    with service.database() as conn:
        conn.execute("INSERT INTO messages(id,thread_id,role,content,created_at,mode) "
                     "VALUES (?,?, 'user','Earlier discussion of graph traversal',?, 'chat')",
                     (service.identifier(), thread, service.now()))

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, path):
            assert path.endswith('-agent-checkpoints.sqlite3')
            class Manager:
                async def __aenter__(self):
                    return FakeSaver()
                async def __aexit__(self, *args):
                    pass
            return Manager()

    class FakeCommand:
        def __init__(self, *, resume):
            self.resume = resume

    for name in ('langgraph', 'langgraph.checkpoint', 'langgraph.checkpoint.sqlite',
                 'langgraph.checkpoint.sqlite.aio', 'langgraph.types'):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules['langgraph.checkpoint.sqlite.aio'].AsyncSqliteSaver = FakeSaver
    sys.modules['langgraph.types'].Command = FakeCommand
    calls = []

    class FakeGraph:
        async def ainvoke(self, payload, *, config):
            calls.append((payload, config))
            return {'messages': [SimpleNamespace(type='ai', content='Research plan ready', tool_calls=[])]}

    monkeypatch.setattr(supervisor, 'build_graph', lambda *args: FakeGraph())
    settings = {'provider': 'ollama', 'model': 'mock'}
    initial = asyncio.run(supervisor.run_turn(thread, 'course', [], settings, 'Explore it', resume=False))
    answer = asyncio.run(supervisor.run_turn(thread, 'course', [], settings, 'Focus on BFS', resume=True))
    assert initial[0] == answer[0] == 'Research plan ready'
    assert 'Earlier discussion of graph traversal' in calls[0][0]['messages'][0]['content']
    assert calls[0][0]['messages'][-1]['content'] == 'Explore it'
    assert isinstance(calls[1][0], FakeCommand) and calls[1][0].resume == 'Focus on BFS'
    assert calls[0][1]['configurable']['thread_id'] == calls[1][1]['configurable']['thread_id']
