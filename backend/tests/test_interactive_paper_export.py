"""Full review/download regression tests for the professor-controlled paper builder."""
import asyncio
import io
import time
import threading

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import app as service, generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'paper.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def ok(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code in (200, 201), response.text
    return response.json()


def setup(client, monkeypatch):
    course_id = client.get('/api/courses').json()[0]['id']
    thread = ok(client, 'POST', '/api/threads', json={'course_id': course_id})['id']
    root = f'/api/threads/{thread}/exam/paper'
    cfg = f'/api/threads/{thread}/exam'
    ok(client, 'PATCH', cfg+'/requirements', json={'updates': {
        'faculty_initials': 'FI', 'university_name': 'North South University',
        'exam_type': 'midterm', 'total_marks': 10, 'difficulty': 'moderate',
        'question_formats': [{'kind': 'mcq', 'count': 2, 'marks_each': 5}], 'material_scope': 'general'}})
    ok(client, 'POST', cfg+'/approve')
    ok(client, 'POST', root+'/prepare')
    ok(client, 'POST', root+'/approve')
    monkeypatch.setattr(service, 'model_credentials', lambda settings: ('http://test/', {}))
    with service.database() as conn:
        conn.execute("UPDATE workspace_settings SET model='mock-model' WHERE id=1")
    return root, thread


def complete(client, root, monkeypatch):
    count = {'n': 0}
    async def fake_generate(*_args, **_kwargs):
        count['n'] += 1
        return generation.GeneratedQuestion(
            stem=f'Explain graph traversal principle number {count["n"]}?',
            options=['Breadth first search', 'Depth first search', 'Binary search', 'Hashing'],
            correct_answer='Breadth first search',
            explanation='A breadth-first search visits nodes in breadth order.', source_ids=[])
    monkeypatch.setattr(generation, 'generate_question', fake_generate)
    for i in range(2):
        candidate = ok(client, 'POST', root+'/next')
        accepted = ok(client, 'POST', root+'/accept', json={'candidate_id': candidate['candidate_id']})
        assert accepted['accepted_marks'] == (i+1)*5
        # Replaying the accept request is safe even if the original response was lost.
        repeated = ok(client, 'POST', root+'/accept', json={'candidate_id': candidate['candidate_id']})
        assert len(repeated['questions']) == i+1
    assert accepted['status'] == 'complete'
    return accepted


def test_final_approval_and_both_file_formats(client, monkeypatch):
    root, thread = setup(client, monkeypatch)
    assert client.post(root+'/final-approve').status_code == 409
    draft = complete(client, root, monkeypatch)
    assert draft['final_approved'] is False
    assert client.get(root+'/export/student.pdf').status_code == 409
    paper = ok(client, 'POST', root+'/final-approve')
    assert paper['final_approved'] is True
    assert ok(client, 'GET', root)['final_approved'] is True
    for format in ('docx', 'pdf'):
        student = client.get(root+f'/export/student.{format}')
        instructor = client.get(root+f'/export/instructor.{format}')
        assert student.status_code == instructor.status_code == 200
        assert 'attachment' in student.headers['content-disposition']
        assert student.headers['cache-control'] == 'no-store'
        if format == 'docx':
            def text(response):
                doc = Document(io.BytesIO(response.content))
                return '\n'.join(p.text for p in doc.paragraphs)
        else:
            def text(response):
                return '\n'.join(page.get_text() for page in fitz.open(stream=response.content, filetype='pdf'))
        std, ins = text(student), text(instructor)
        for expected in ('North South University', 'Faculty: FI', 'Full marks: 10', 'Section 1',
                         'Q1.', 'Q2.', '5 marks', 'Breadth first search'):
            assert expected in std, (format, expected, std)
        assert 'Answer:' not in std and 'Explanation:' not in std
        assert 'Answer:' in ins and 'Explanation:' in ins


def test_paper_edit_invalidates_download_and_restart_preserves_state(client, monkeypatch):
    root, _ = setup(client, monkeypatch)
    paper = complete(client, root, monkeypatch)
    ok(client, 'POST', root+'/final-approve')
    changed = paper['questions'][0]
    content = changed['content']
    edited = {key: content[key] for key in ('stem', 'options', 'correct_answer', 'explanation')}
    edited['stem'] = 'Professor edited the graph traversal algorithm question?'
    revised = ok(client, 'PATCH', root+f'/questions/{changed["id"]}', json=edited)
    assert revised['final_approved'] is False
    assert revised['questions'][0]['version'] == 2
    assert client.get(root+'/export/instructor.docx').status_code == 409
    # Simulate reconnect / browser refresh: authoritative state is persisted in SQLite.
    recovered = ok(client, 'GET', root)
    assert recovered['accepted_marks'] == 10 and recovered['questions'][0]['content']['stem'] == edited['stem']
    ok(client, 'POST', root+'/final-approve')
    assert client.get(root+'/export/student.docx').status_code == 200
    ids = [question['id'] for question in recovered['questions']]
    assert ok(client, 'PUT', root+'/order', json={'question_ids': ids[::-1]})['final_approved'] is False
    assert client.get(root+'/export/student.pdf').status_code == 409
    ok(client, 'POST', root+'/final-approve')
    removed = ok(client, 'DELETE', root+f'/questions/{ids[0]}')
    assert removed['final_approved'] is False and removed['status'] == 'ready'
    assert client.post(root+'/final-approve').status_code == 409


def test_timeout_keeps_saved_questions_and_allows_retry(client, monkeypatch):
    root, _ = setup(client, monkeypatch)
    count = {'n': 0}
    async def flaky(*_args, **_kwargs):
        count['n'] += 1
        if count['n'] == 2:
            raise asyncio.TimeoutError()
        return generation.GeneratedQuestion(stem='A question about linked list traversal correctness?',
            options=['A', 'B', 'C', 'D'], correct_answer='A',
            explanation='The correct choice is A because of traversal order.', source_ids=[])
    monkeypatch.setattr(generation, 'generate_question', flaky)
    candidate = ok(client, 'POST', root+'/next')
    ok(client, 'POST', root+'/accept', json={'candidate_id': candidate['candidate_id']})
    bad = client.post(root+'/next')
    assert bad.status_code == 504 and 'safe' in bad.json()['detail']
    recovered = ok(client, 'GET', root)
    assert recovered['status'] == 'ready' and len(recovered['questions']) == 1
    assert ok(client, 'GET', root+'/events')[0]['event'] == 'generation_failed'


def test_cancellation_ignores_late_model_response(client, monkeypatch):
    root, _ = setup(client, monkeypatch)
    gate = threading.Event()
    async def blocked(*_args, **_kwargs):
        gate.set()
        await asyncio.sleep(0.3)
        return generation.GeneratedQuestion(stem='Question generated too late to be accepted?',
            options=['A', 'B', 'C', 'D'], correct_answer='A',
            explanation='A is the correct answer for this question.', source_ids=[])
    monkeypatch.setattr(generation, 'generate_question', blocked)
    result = {}
    def run_generation():
        result['response'] = client.post(root+'/next')
    thread = threading.Thread(target=run_generation)
    thread.start()
    assert gate.wait(3)
    cancelled = ok(client, 'POST', root+'/cancel')
    assert cancelled['status'] == 'ready'
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result['response'].status_code == 409
    paper = ok(client, 'GET', root)
    assert paper['candidate'] is None and paper['questions'] == []
