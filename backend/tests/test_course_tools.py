"""Read-only course tools exposed to Deep Agents supervisors."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from backend import app as service, course_tools, exam_planner, generation


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'tools.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def upload(client, cid, name, text):
    response = client.post(f'/api/courses/{cid}/documents',
                           files={'file': (name, text.encode())})
    assert response.status_code == 201, response.text
    doc_id = response.json()['id']
    for _ in range(150):
        status = client.get(f'/api/courses/{cid}/documents/{doc_id}').json()['status']
        if status in ('ready', 'failed'):
            break
        time.sleep(0.02)
    assert status == 'ready', status
    return doc_id


def test_list_and_ordered_read_are_course_scoped(client):
    cid = client.get('/api/courses').json()[0]['id']
    other = client.post('/api/courses', json={'name': 'Other'}).json()['id']
    deck = upload(client, cid, 'Ch 3 PPT.txt', 'Queues process items first-in-first-out.')
    upload(client, other, 'Ch 3 PPT.txt', 'Private other-course content')

    listed = course_tools.list_course_documents(cid)
    assert [item['filename'] for item in listed] == ['Ch 3 PPT.txt']
    assert listed[0]['status'] == 'ready' and listed[0]['chunk_count'] >= 1

    chunks = course_tools.read_document_chunks(cid, 'Ch 3')
    assert chunks and all(source['document_id'] == deck for source in chunks)
    assert 'first-in-first-out' in chunks[0]['snippet']
    # Filename resolution never crosses the course boundary.
    assert course_tools.read_document_chunks(other, 'nothing-matches-here') == []


def test_agent_tool_wrappers_have_expected_names():
    tools = course_tools.agent_tools('any-course')
    assert {tool.name for tool in tools} == {'list_course_documents', 'read_document_chunks'}


def test_assessment_supervisor_receives_course_tools(client, monkeypatch):
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        raise RuntimeError('stop after capture')

    monkeypatch.setattr('deepagents.create_deep_agent', capture)

    class _FakeCM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    class _FakeSaver:
        @classmethod
        def from_conn_string(cls, path):
            return _FakeCM()

    monkeypatch.setattr('langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver', _FakeSaver)
    monkeypatch.setattr(generation, 'provider_details',
                        lambda settings: ('http://local/v1/chat/completions', {'Authorization': 'Bearer x'}))
    job = {'id': 'job-tools', 'course_id': 'c-missing', 'document_ids': '[]', 'prompt': 'Make a quiz'}
    with pytest.raises(RuntimeError, match='stop after capture'):
        asyncio.run(generation.create_blueprint(job, {'provider': 'ollama', 'model': 'm'}))
    names = {getattr(tool, 'name', None) for tool in captured.get('tools', [])}
    assert {'search_course_materials', 'list_course_documents', 'read_document_chunks'} <= names


def test_planner_agent_receives_course_tools(monkeypatch):
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        raise RuntimeError('stop after capture')

    monkeypatch.setattr('deepagents.create_deep_agent', capture)
    monkeypatch.setattr(generation, 'provider_details',
                        lambda settings: ('http://local/v1/chat/completions', {}))
    context = {'scope': 'general', 'slots': [], 'blueprint': {'sections': []}}
    result = asyncio.run(exam_planner.enrich(context, {'provider': 'ollama', 'model': 'm'}, {},
                                             course_id='c1'))
    assert result == context  # advisory fallback stays intact
    names = {getattr(tool, 'name', None) for tool in captured.get('tools', [])}
    assert {'inspect_slot_evidence', 'list_course_documents', 'read_document_chunks'} <= names
