"""Regression coverage for refresh and diagnostic behavior in the interactive workspace."""
import re

from fastapi.testclient import TestClient
from backend import app as service


def test_saved_exam_survives_server_restart_and_all_refresh_reads_work(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'questgen.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as client:
        assert client.get('/api/health').json() == {'status': 'ok'}
        course_id = client.get('/api/courses').json()[0]['id']
        thread_id = client.post('/api/threads', json={'course_id': course_id}).json()['id']
        root = f'/api/threads/{thread_id}'
        req = {'faculty_initials': 'FI', 'university_name': 'Example University',
               'exam_type': 'midterm', 'total_marks': 5, 'difficulty': 'moderate',
               'question_formats': [{'kind': 'mcq', 'count': 10, 'marks_each': 0.5}],
               'material_scope': 'general'}
        assert client.post(root + '/exam/start').status_code == 200
        assert client.patch(root + '/exam/requirements', json={'updates': req}).status_code == 200
        assert client.post(root + '/exam/approve').status_code == 200
        paper = client.post(root + '/exam/paper/prepare')
        assert paper.status_code == 200, paper.text
        assert paper.json()['total_marks'] == 5
    # Force the application to run startup migrations a second time.
    with TestClient(service.app) as client:
        for route in (root + '/messages', root + '/exam', root + '/exam/paper'):
            result = client.get(route)
            assert result.status_code == 200, (route, result.text)
        assert client.get(root + '/exam').json()['status'] == 'approved'
        assert client.get(root + '/exam/paper').json()['total_marks'] == 5


def test_unhandled_error_has_safe_reference_and_server_log(caplog):
    import asyncio
    from starlette.requests import Request
    scope = {'type': 'http', 'method': 'GET', 'path': '/api/threads/example/exam/paper',
             'raw_path': b'/api/threads/example/exam/paper', 'query_string': b'',
             'headers': [], 'scheme': 'http', 'server': ('localhost', 8000)}
    request = Request(scope)
    response = asyncio.run(service.report_unhandled_error(request, RuntimeError('private internal message')))
    assert response.status_code == 500
    assert b'private internal message' not in response.body
    assert re.search(rb'reference [0-9a-f]{10}', response.body)
    assert 'QuestGen request failed' in caplog.text
