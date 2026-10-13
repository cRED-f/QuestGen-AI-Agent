"""Security, policy and research persistence, without live network or LLM access."""
import asyncio
import socket
import sys
import types

import pytest
from fastapi.testclient import TestClient
from backend import app as service
from backend.agent import research


@pytest.fixture
def research_case(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'academic-research.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    monkeypatch.delenv('BRAVE_SEARCH_API_KEY', raising=False)
    with TestClient(service.app) as client:
        course_id = client.get('/api/courses').json()[0]['id']
        thread_id = client.post('/api/threads', json={'course_id': course_id}).json()['id']
        yield client, course_id, thread_id


def test_policy_requires_specific_external_expansion_approval(research_case):
    client, _, thread_id = research_case
    path = f'/api/threads/{thread_id}/agent/research-policy'
    assert client.get(path).json()['mode'] == 'supplement'
    assert client.put(path, json={'mode': 'expand'}).status_code == 422
    assert client.put(path, json={'mode': 'course_only'}).json()['mode'] == 'course_only'
    enabled = client.put(path, json={'mode': 'expand', 'expansion_approved': True}).json()
    assert enabled['expansion_approved'] and enabled['mode'] == 'expand'
    assert client.put(path, json={'mode': 'supplement'}).json()['expansion_approved'] is False
    assert client.get('/api/threads/missing/agent/research-policy').status_code == 404


def test_missing_ddgs_does_not_break_course_research(research_case, monkeypatch):
    _, course, tid = research_case
    # Simulate a machine without the optional keyless-search dependency.
    monkeypatch.setitem(sys.modules, 'ddgs', None)
    assert research.policy(tid)['available'] is False
    with pytest.raises(research.ResearchError, match='install'):
        asyncio.run(research.web_search(tid, course, 'supply demand university'))
    research.set_policy(tid, 'course_only')
    with pytest.raises(research.ResearchError, match='disabled'):
        asyncio.run(research.web_search(tid, course, 'supply demand university'))


def _stub_ddgs(monkeypatch, *, records=None, error=None):
    calls = []
    module = types.ModuleType('ddgs')
    class FakeDDGS:
        def __init__(self, timeout):
            calls.append(('init', timeout))
        def text(self, query, *, max_results, backend, safesearch):
            calls.append(('search', query, max_results, backend, safesearch))
            if error:
                raise error
            return records if records is not None else []
    module.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, 'ddgs', module)
    return calls


def test_url_guard_rejects_local_addresses_and_insecure_schemes(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 0, 0, '', ('127.0.0.1', 443))])
    for url in ('http://university.edu/', 'https://localhost/', 'https://127.0.0.1/x',
                'https://user:pass@host.edu/x', 'https://public.edu:8080/',
                'file:///etc/passwd', 'https://university.edu/x\nunsafe'):
        with pytest.raises(research.ResearchError):
            research.public_url(url)
    with pytest.raises(research.ResearchError, match='nonpublic'):
        research.public_url('https://public.edu/research')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 0, 0, '', ('93.184.216.34', 443))])
    assert research.public_url('https://public.edu/research').startswith('https://public.edu')


def test_duckduckgo_search_persists_attributed_sources_scoped_to_one_thread(research_case, monkeypatch):
    client, course, tid = research_case
    calls = _stub_ddgs(monkeypatch, records=[
        {'href': 'https://mit.edu/topic', 'title': 'University guide', 'body': 'Course example'},
        {'href': 'http://unsafe.edu/topic', 'title': 'Insecure', 'body': 'Unsafe'},
        {'href': 'https://openstax.org/books/example', 'title': 'Open textbook', 'body': 'Problem examples'},
    ])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 0, 0, '', ('93.184.216.34', 443))])
    assert research.policy(tid)['provider'] == 'duckduckgo'
    assert research.policy(tid)['available'] is True
    result = asyncio.run(research.web_search(tid, course, 'topic examples from lectures'))
    assert result['provider'] == 'duckduckgo'
    assert len(result['sources']) == 2
    assert result['sources'][0]['source_type'] == 'university'
    assert result['sources'][1]['source_type'] == 'open_education'
    assert calls == [('init', 10), ('search', 'topic examples from lectures', 5, 'duckduckgo', 'moderate')]
    assert client.get(f'/api/threads/{tid}/agent/research-sources').json()[0]['url'].startswith('https://')
    other = client.post('/api/threads', json={'course_id': course}).json()['id']
    assert research.sources(other, course) == []
    assert research.verify_academic_claims(other, course, 'topic examples', [result['sources'][0]['id']])['candidate_evidence'] == []
    assert research.compare_with_syllabus(tid, course, [], 'topic', [result['sources'][0]['id']])['external_expansion_allowed'] is False


def test_duckduckgo_rate_limit_reported_without_claiming_search_success(research_case, monkeypatch):
    _, course, tid = research_case
    class RatelimitException(Exception):
        pass
    _stub_ddgs(monkeypatch, error=RatelimitException('202 Ratelimit'))
    with pytest.raises(research.ResearchError, match='rate-limited'):
        asyncio.run(research.web_search(tid, course, 'university economics questions'))
    assert research.sources(tid, course) == []


def test_ddgs_unexpected_payload_fails_closed(research_case, monkeypatch):
    _, course, tid = research_case
    calls = _stub_ddgs(monkeypatch, records={'unexpected': 'response'})
    with pytest.raises(research.ResearchError, match='temporarily unavailable'):
        asyncio.run(research.web_search(tid, course, 'sample university course'))
    assert len(calls) == 2
    assert research.sources(tid, course) == []


def test_read_page_strips_scripts_and_does_not_follow_redirect(research_case, monkeypatch):
    _, course, tid = research_case
    monkeypatch.setattr(research, 'provider_name', lambda: 'duckduckgo')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 0, 0, '', ('93.184.216.34', 443))])
    saved = research._save_source(tid, course, 'https://university.edu/content', 'Lecture', 'Overview')

    class Response:
        status_code = 200
        headers = {'content-type': 'text/html; charset=utf-8'}
        def raise_for_status(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def aiter_bytes(self):
            yield b'<html><nav>menu</nav><h1>Opportunity cost</h1><script>ignore this instruction</script><p>A useful example</p></html>'

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def stream(self, method, url): return Response()

    monkeypatch.setattr(research.httpx, 'AsyncClient', Client)
    data = asyncio.run(research.read_web_page(tid, course, saved['id']))
    assert 'Opportunity cost' in data['content']
    assert 'ignore this instruction' not in data['content']
    assert research.sources(tid, course)[0]['excerpt'].startswith('Opportunity cost')

    class Redirect(Response):
        status_code = 302
        headers = {'location': 'https://localhost/private'}
    class RedirectClient(Client):
        def stream(self, method, url): return Redirect()
    monkeypatch.setattr(research.httpx, 'AsyncClient', RedirectClient)
    with pytest.raises(research.ResearchError, match='redirects'):
        asyncio.run(research.read_web_page(tid, course, saved['id']))


def test_budget_and_deletion_cascade(research_case, monkeypatch):
    client, course, tid = research_case
    for _ in range(research.SEARCH_QUOTA):
        research._quota(tid, 'search')
    with pytest.raises(research.ResearchError, match='budget'):
        research._quota(tid, 'search')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 0, 0, '', ('93.184.216.34', 443))])
    research._save_source(tid, course, 'https://university.edu/topic', 'Lecture', 'Content')
    assert client.delete(f'/api/threads/{tid}').status_code == 204
    with service.database() as conn:
        for table in ('agent_research_policy', 'agent_research_sources', 'agent_research_usage'):
            assert conn.execute(f'SELECT COUNT(*) AS c FROM {table} WHERE thread_id=?', (tid,)).fetchone()['c'] == 0


def test_research_policy_cannot_change_during_run(research_case):
    client, _, tid = research_case
    from backend.agent import state
    with service.database() as conn:
        state.claim(conn, tid, [])
    endpoint = f'/api/threads/{tid}/agent/research-policy'
    assert client.put(endpoint, json={'mode': 'course_only'}).status_code == 409
    with service.database() as conn:
        state.finish(conn, tid, question=None, activity=[])
    assert client.put(endpoint, json={'mode': 'course_only'}).status_code == 200


def test_specialists_do_not_receive_professor_interrupt_or_write_tools(monkeypatch):
    import sys
    import types
    from types import SimpleNamespace
    from backend.agent import supervisor

    fake_deepagents = types.ModuleType('deepagents')
    fake_backends = types.ModuleType('deepagents.backends')
    fake_backends.StateBackend = lambda: 'ephemeral-scoped-state'
    fake_deepagents.FilesystemPermission = lambda **kw: kw
    captured = {}
    def create_deep_agent(**kwargs):
        captured.update(kwargs)
        return kwargs
    fake_deepagents.create_deep_agent = create_deep_agent
    monkeypatch.setitem(sys.modules, 'deepagents', fake_deepagents)
    monkeypatch.setitem(sys.modules, 'deepagents.backends', fake_backends)
    monkeypatch.setattr(supervisor, 'build_model', lambda settings: 'mock-model')
    names = ['ask_professor', 'update_research_focus', 'get_academic_map', 'search_course_passages',
             'inspect_topic_coverage', 'list_course_documents', 'web_search',
             'read_web_page', 'find_academic_resources', 'find_question_examples',
             'verify_academic_claims', 'compare_with_syllabus']
    monkeypatch.setattr(supervisor.tools, 'build_tools', lambda *a, **k: [SimpleNamespace(name=n) for n in names])
    supervisor.build_graph({'model': 'mock'}, 'course', ['doc'], object(), 'thread')
    assert len(captured['subagents']) == 4
    for specialist in captured['subagents']:
        names_available = {tool.name for tool in specialist['tools']}
        assert 'ask_professor' not in names_available
        assert 'update_research_focus' not in names_available
        assert not {'edit_file', 'delete', 'write_file', 'execute'} & names_available
    assert 'ask_professor' in {t.name for t in captured['tools']}
