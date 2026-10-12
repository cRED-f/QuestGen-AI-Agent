"""Fractional marks work from the HITL mix through the saved paper and exports."""
import io

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import app as service, generation
from backend.marks import to_cents


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'fractional.sqlite'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def exam(client, *, marks=5):
    course_id = client.get('/api/courses').json()[0]['id']
    thread_id = client.post('/api/threads', json={'course_id': course_id}).json()['id']
    return f'/api/threads/{thread_id}/exam'


def test_half_mark_mix_is_saved_and_approved(client):
    root = exam(client)
    assert client.post(root+'/start').status_code == 200
    requirements = {'faculty_initials': 'FI', 'university_name': 'Example University',
                    'exam_type': 'quiz', 'total_marks': 5, 'difficulty': 'moderate'}
    assert client.patch(root+'/requirements', json={'updates': requirements}).status_code == 200
    sections = [{'kind': 'mcq', 'count': 4, 'marks_each': 0.5},
                {'kind': 'short_answer', 'count': 1, 'marks_each': 3}]
    response = client.post(root+'/quick-reply', json={'updates': {'question_formats': sections},
                                                     'answer': '4 MCQs x 0.5 marks and 1 short answer x 3 marks'})
    assert response.status_code == 200, response.text
    assert response.json()['session']['missing'] == ['material_scope']
    saved = client.get(root).json()['requirements']['question_formats']
    assert saved == sections
    assert client.post(root+'/quick-reply', json={
        'updates': {'material_scope': 'general'}, 'answer': 'General knowledge'}).status_code == 200
    assert client.post(root+'/approve').status_code == 200
    prepared = client.post(root+'/paper/prepare')
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()['total_marks'] == 5
    assert prepared.json()['blueprint']['sections'][0]['marks_each'] == 0.5


@pytest.mark.parametrize('invalid', [0, -0.5, 0.001, 0.555, 100.01])
def test_invalid_fractional_marks_rejected(client, invalid):
    root = exam(client)
    assert client.post(root+'/start').status_code == 200
    result = client.post(root+'/quick-reply', json={'updates': {
        'question_formats': [{'kind': 'mcq', 'count': 10, 'marks_each': invalid}]},
        'answer': 'Invalid marks'})
    assert result.status_code == 422
    assert client.get(root).json()['requirements'] == {}
    thread_id = root.split('/')[3]
    assert client.get(f'/api/threads/{thread_id}/messages').json() == []


def test_decimal_total_has_exact_allocation(client):
    root = exam(client)
    assert client.post(root+'/start').status_code == 200
    answer = client.patch(root+'/requirements', json={'updates': {
        'faculty_initials': 'FI', 'university_name': 'Example University',
        'exam_type': 'quiz', 'total_marks': 0.3, 'difficulty': 'easy',
        'question_formats': [{'kind': 'mcq', 'count': 1, 'marks_each': 0.1},
                             {'kind': 'short_answer', 'count': 1, 'marks_each': 0.2}],
        'material_scope': 'general'}})
    assert answer.status_code == 200, answer.text
    assert answer.json()['missing'] == []
    assert client.post(root+'/approve').status_code == 200
    assert client.post(root+'/paper/prepare').json()['total_marks'] == 0.3


def test_exam_turn_recognizes_spoken_half_mark_distribution(client):
    root = exam(client)
    client.post(root+'/start')
    client.patch(root+'/requirements', json={'updates': {
        'faculty_initials': 'FI', 'university_name': 'Example University',
        'exam_type': 'quiz', 'total_marks': 5, 'difficulty': 'moderate'}})
    result = client.post(root+'/turn', json={'content': '10 MCQs x 0.5 marks'})
    assert result.status_code == 200, result.text
    assert result.json()['session']['requirements']['question_formats'][0]['marks_each'] == 0.5
    assert result.json()['session']['missing'] == ['material_scope']


def test_half_mark_candidate_and_export_roundtrip(client, monkeypatch):
    root = exam(client)
    client.post(root+'/start')
    cfg = {'faculty_initials': 'FI', 'university_name': 'Example University',
           'exam_type': 'quiz', 'total_marks': 1, 'difficulty': 'moderate',
           'question_formats': [{'kind': 'mcq', 'count': 2, 'marks_each': 0.5}],
           'material_scope': 'general'}
    assert client.patch(root+'/requirements', json={'updates': cfg}).status_code == 200
    assert client.post(root+'/approve').status_code == 200
    assert client.post(root+'/paper/prepare').status_code == 200
    assert client.post(root+'/paper/approve').status_code == 200
    monkeypatch.setattr(service, 'model_credentials', lambda settings: ('http://test/', {}))
    with service.database() as conn:
        conn.execute("UPDATE workspace_settings SET model='mock-model' WHERE id=1")
    count = 0
    async def fake_question(_settings, plan, _sources, _feedback=''):
        nonlocal count
        count += 1
        assert plan.marks == 0.5
        return generation.GeneratedQuestion(
            stem=f'Question {count} about the algorithm and its basic operation?',
            options=['Answer A', 'Answer B', 'Answer C', 'Answer D'],
            correct_answer='Answer B',
            explanation='Answer B is correct for this simple case.', source_ids=[])
    monkeypatch.setattr(generation, 'generate_question', fake_question)
    for index in range(2):
        generated = client.post(root+'/paper/next')
        assert generated.status_code == 200, generated.text
        assert generated.json()['next_slot']['marks'] == 0.5
        accepted = client.post(root+'/paper/accept', json={'candidate_id': generated.json()['candidate_id']})
        assert accepted.status_code == 200, accepted.text
        paper = accepted.json()
        assert paper['accepted_marks'] == (index + 1) * 0.5
        assert paper['remaining_marks'] == 1 - (index + 1) * 0.5
    assert paper['status'] == 'complete'
    assert client.post(root+'/paper/final-approve').status_code == 200
    for filetype in ('docx', 'pdf'):
        response = client.get(root+f'/paper/export/student.{filetype}')
        assert response.status_code == 200, response.text
        if filetype == 'docx':
            output = '\n'.join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
        else:
            output = '\n'.join(p.get_text() for p in fitz.open(stream=response.content, filetype='pdf'))
        assert 'Full marks: 1' in output
        assert output.count('0.5 marks') == 2
        assert '1.0 marks' not in output


def test_exact_decimal_math():
    assert to_cents(0.5) == 50
    assert to_cents(0.01) == 1
    with pytest.raises(ValueError, match='decimal places'):
        to_cents(0.015)
