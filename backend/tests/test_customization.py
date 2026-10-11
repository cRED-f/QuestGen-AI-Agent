"""Phase 4 end-to-end route tests, no live LLM provider required."""
import io
import json
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfReader

from backend import app as module, customization, generation


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'test.sqlite3'))
    with TestClient(module.app) as client:
        course = client.get('/api/courses').json()[0]['id']
        thread = client.post('/api/threads', json={'course_id': course}).json()['id']
        job = module.identifier()
        blueprint = generation.AssessmentBlueprint(title='Data Structures Exam', total_marks=10,
            questions=[generation.QuestionPlan(topic='binary search', kind='mcq', difficulty='medium',
                bloom_level='apply', marks=5), generation.QuestionPlan(topic='linked lists',
                kind='short_answer', difficulty='easy', bloom_level='understand', marks=5)])
        with module.database() as conn:
            t = module.now()
            conn.execute('''INSERT INTO assessment_jobs(id,thread_id,course_id,prompt,provider,model,status,stage,blueprint,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'completed','completed',?,?,?)''',
                (job, thread, course, 'Create two questions', 'ollama', 'test-model', blueprint.model_dump_json(), t, t))
            for i, plan in enumerate(blueprint.questions, start=1):
                content = dict(plan.model_dump(),
                    stem=f'Explain how {plan.topic} works and provide a useful real-world example.',
                    options=['A sorted array', 'An unsorted heap', 'An unordered set', 'A random graph'] if i == 1 else [],
                    correct_answer='A sorted array' if i == 1 else 'A linked list uses linked nodes.',
                    explanation='The correct answer follows from the underlying data structure.',
                    source_ids=[], sources=[], grounded=False)
                conn.execute('''INSERT INTO assessment_questions(id,job_id,ordinal,content,created_at)
                    VALUES(?,?,?,?,?)''', (f'q{i}', job, i, json.dumps(content), t))
                conn.execute('''INSERT INTO assessment_question_versions(question_id,version,content,change_type,instruction,created_at)
                    VALUES(?,1,?,'generated','Initial question',?)''', (f'q{i}', json.dumps(content), t))
        prefix = f'/api/threads/{thread}/generations/{job}'
        yield client, prefix, job


def test_edit_versions_locks_and_conflicts(ctx):
    client, prefix, _ = ctx
    qpath = f'{prefix}/questions/q1'
    initial = client.get(qpath + '/versions').json()
    original = initial['content']
    revised = {k: original[k] for k in ('stem', 'options', 'correct_answer', 'explanation')}
    revised['stem'] = 'Explain binary search using a sorted collection with five elements.'
    result = client.put(qpath, json={'expected_version': 1, **revised})
    assert result.status_code == 200, result.text
    assert result.json()['version'] == 2
    assert len(client.get(qpath + '/versions').json()['versions']) == 2
    assert client.put(qpath, json={'expected_version': 1, **revised}).status_code == 409
    assert client.put(qpath + '/lock?locked=true', json={'expected_version': 2}).json()['locked']
    assert client.put(qpath, json={'expected_version': 2, **revised}).status_code == 409
    assert client.put(qpath + '/lock?locked=false', json={'expected_version': 2}).json()['locked'] is False
    invalid = dict(revised, options=['same', 'same', 'third', 'fourth'])
    assert client.put(qpath, json={'expected_version': 2, **invalid}).status_code == 422
    assert client.get(qpath + '/versions').json()['version'] == 2


def test_review_and_student_export_gate(ctx):
    client, prefix, _ = ctx
    student = prefix + '/export/student.pdf'
    assert client.get(student).status_code == 409
    assert client.put(prefix + '/questions/q1/review', json={'expected_version': 1, 'decision':'approved'}).status_code == 200
    assert client.get(student).status_code == 409
    assert client.put(prefix + '/questions/q2/review', json={'expected_version': 1, 'decision':'approved'}).status_code == 200
    pdf = client.get(student)
    assert pdf.status_code == 200
    assert pdf.headers['content-type'].startswith('application/pdf')
    reader = PdfReader(io.BytesIO(pdf.content))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'binary search' in text
    assert 'Answer:' not in text and 'Explanation:' not in text
    assert 'A linked list uses linked nodes.' not in text
    instructor_pdf = client.get(prefix + '/export/instructor.pdf')
    assert 'Answer:' in PdfReader(io.BytesIO(instructor_pdf.content)).pages[0].extract_text()
    student_docx = client.get(prefix + '/export/student.docx')
    document = Document(io.BytesIO(student_docx.content))
    combined = '\n'.join(p.text for p in document.paragraphs)
    assert 'Answer:' not in combined and 'A linked list uses linked nodes.' not in combined
    instructor = Document(io.BytesIO(client.get(prefix + '/export/instructor.docx').content))
    assert 'Answer:' in '\n'.join(p.text for p in instructor.paragraphs)
    # Any edit invalidates approval and blocks student export again.
    original = client.get(prefix + '/questions/q2/versions').json()['content']
    change = {k: original[k] for k in ('stem', 'options', 'correct_answer', 'explanation')}
    change['stem'] = 'Explain how a linked list stores nodes and how traversal operates.'
    assert client.put(prefix + '/questions/q2', json={'expected_version': 1, **change}).status_code == 200
    assert client.get(student).status_code == 409
    assert client.get(prefix + '/questions/q2/versions').json()['review_status'] == 'pending'


def test_reject_unlock_and_audit(ctx):
    client, prefix, _ = ctx
    q = prefix + '/questions/q1'
    result = client.put(q + '/review', json={
        'expected_version':1, 'decision':'rejected','note':'Not suitable for midterm'})
    assert result.status_code == 200
    assert result.json()['review_note'] == 'Not suitable for midterm'
    with module.database() as conn:
        actions = [r['action'] for r in conn.execute('SELECT action FROM assessment_question_audit WHERE question_id=?', ('q1',))]
        assert 'rejected' in actions


def test_targeted_ai_regeneration_is_bounded_and_atomic(ctx, monkeypatch):
    client, prefix, _ = ctx
    count = {'calls':0}
    async def model_json(*args, **kwargs):
        count['calls'] += 1
        return { 'stem': 'What is the best prerequisite for binary search in a collection?',
                'options': ['Sorted array', 'Linked nodes', 'Random graph', 'Heap only'],
                'correct_answer':'Sorted array', 'explanation':'Sorted input enables correct binary search.',
                'source_ids': [] }
    async def review(*args, **kwargs):
        return generation.QualityReview(passed=True)
    monkeypatch.setattr(generation, 'model_json', model_json)
    monkeypatch.setattr(generation, 'review_question', review)
    endpoint = prefix + '/questions/q1/regenerate'
    result = client.post(endpoint, json={'expected_version':1,'instruction':'Use an applied reasoning scenario'})
    assert result.status_code == 200, result.text
    assert result.json()['version'] == 2
    assert count['calls'] == 1
    assert client.get(prefix + '/questions/q2/versions').json()['version'] == 1
    assert client.post(endpoint, json={'expected_version':1,'instruction':'try again'}).status_code == 409
    assert client.put(prefix + '/questions/q1/lock', json={'expected_version':2}).status_code == 200
    assert client.post(endpoint, json={'expected_version':2,'instruction':'try again'}).status_code == 409


def test_conversation_targets_specific_question(ctx, monkeypatch):
    client, prefix, _ = ctx
    async def mock_model_json(settings, system, request):
        return {'actions':[{'ordinal':2,'operation':'lock','instruction':''}]}
    monkeypatch.setattr(generation, 'model_json', mock_model_json)
    result = client.post(prefix + '/customize', json={'instruction':'Lock question 2 only, leave the rest alone'})
    assert result.status_code == 200, result.text
    assert result.json()['actions'] == [{'ordinal':2,'operation':'lock','version':1,'review_status':'pending'}]
    assert client.get(prefix + '/questions/q1/versions').json()['locked'] is False
    assert client.get(prefix + '/questions/q2/versions').json()['locked'] is True
    thread_messages = client.get(prefix.split('/generations/')[0] + '/messages').json()
    assert any('Lock question 2' in m['content'] for m in thread_messages)


def test_course_and_thread_scoped_routes(ctx):
    client, prefix, job = ctx
    wrong = f'/api/threads/nonexistent/generations/{job}'
    assert client.get(wrong + '/export/instructor.pdf').status_code == 404
    assert client.put(wrong + '/questions/q1/review',json={'expected_version':1,'decision':'approved'}).status_code == 404


def test_restore_from_history_creates_new_version(ctx):
    client, prefix, _ = ctx
    endpoint = prefix + '/questions/q1'
    prior = client.get(endpoint + '/versions').json()['content']
    body = {k: prior[k] for k in ('stem', 'options', 'correct_answer', 'explanation')}
    body['stem'] = 'Explain why a sorted array is necessary for binary search algorithms.'
    assert client.put(endpoint, json={**body, 'expected_version':1}).status_code == 200
    restored = client.post(endpoint + '/restore', json={'expected_version':2, 'target_version':1})
    assert restored.status_code == 200
    assert restored.json()['version'] == 3
    assert restored.json()['content']['stem'] == prior['stem']
    assert [v['version'] for v in restored.json()['versions']] == [3,2,1]
    assert client.post(endpoint + '/restore', json={'expected_version':2, 'target_version':1}).status_code == 409


def test_conversation_actions_commit_atomically(ctx, monkeypatch):
    client, prefix, _ = ctx
    async def mock_plan(*args, **kwargs):
        return {'actions': [
            {'ordinal':1,'operation':'approve','instruction':''},
            {'ordinal':2,'operation':'lock','instruction':''},
        ]}
    monkeypatch.setattr(generation, 'model_json', mock_plan)
    result = client.post(prefix + '/customize', json={'instruction':'Approve question 1 and lock question 2'})
    assert result.status_code == 200
    assert client.get(prefix + '/questions/q1/versions').json()['review_status'] == 'approved'
    assert client.get(prefix + '/questions/q2/versions').json()['locked'] is True
