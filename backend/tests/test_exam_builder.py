"""Professor-controlled question-by-question paper assembly regression coverage."""
import pytest
from fastapi.testclient import TestClient

from backend import app as service, exam_builder, generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'exam-builder.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def setup(client, *, marks=10):
    course = client.get('/api/courses').json()[0]['id']
    tid = client.post('/api/threads', json={'course_id': course}).json()['id']
    root = f'/api/threads/{tid}/exam'
    formats = [{'kind': 'mcq', 'count': marks // 5, 'marks_each': 5}]
    patch = {'updates': {'faculty_initials': 'FI', 'university_name': 'Example University',
                         'exam_type': 'midterm', 'total_marks': marks, 'difficulty': 'moderate',
                         'question_formats': formats, 'material_scope': 'general'}}
    assert client.patch(root + '/requirements', json=patch).status_code == 200
    assert client.post(root + '/approve').status_code == 200
    return root


def request(client, method, path, **kwargs):
    result = client.request(method, path, **kwargs)
    assert result.status_code == 200, (result.status_code, result.text)
    return result.json()


def test_blueprint_requires_approval_and_confirms_allocations(client):
    tid = client.post('/api/threads', json={'course_id': client.get('/api/courses').json()[0]['id']}).json()['id']
    assert client.post(f'/api/threads/{tid}/exam/paper/prepare').status_code == 409
    root = setup(client)
    paper = request(client, 'POST', root + '/paper/prepare')
    assert paper['status'] == 'awaiting_approval'
    assert paper['total_marks'] == 10
    assert paper['total_questions'] == 2
    assert client.post(root + '/paper/next').status_code == 409
    edited = paper['blueprint']
    edited['sections'][0]['topic'] = 'Graph traversal'
    paper = request(client, 'PUT', root + '/paper/blueprint', json={'blueprint': edited})
    assert paper['blueprint']['sections'][0]['topic'] == 'Graph traversal'
    bad = {**edited, 'sections': [{**edited['sections'][0], 'count': 4}]}
    assert client.put(root + '/paper/blueprint', json={'blueprint': bad}).status_code == 409
    paper = request(client, 'POST', root + '/paper/approve')
    assert paper['status'] == 'ready'
    assert client.put(root + '/paper/blueprint', json={'blueprint': edited}).status_code == 409


def test_one_candidate_at_a_time_and_durable_paper(client, monkeypatch):
    root = setup(client)
    request(client, 'POST', root + '/paper/prepare')
    request(client, 'POST', root + '/paper/approve')
    counter = {'n': 0}

    async def generate(_settings, plan, _sources, _feedback=''):
        counter['n'] += 1
        return generation.GeneratedQuestion(
            stem=f'Question {counter["n"]} about {plan.topic} and its applications?',
            options=['First', 'Second', 'Third', 'Fourth'], correct_answer='Second',
            explanation='Second is the correct choice for this example.', source_ids=[])

    async def review(*args):
        return generation.QualityReview(passed=True, issues=[])

    monkeypatch.setattr(generation, 'generate_question', generate)
    monkeypatch.setattr(generation, 'review_question', review)
    monkeypatch.setattr(service, 'model_credentials', lambda settings: ('http://test/', {}))
    with service.database() as conn:
        conn.execute("UPDATE workspace_settings SET model='mock-model' WHERE id=1")
    first = request(client, 'POST', root + '/paper/next')
    assert first['candidate']['stem'].startswith('Question 1')
    assert first['questions'] == [] and first['remaining_marks'] == 10
    assert client.post(root + '/paper/next').status_code == 409
    skipped = request(client, 'POST', root + '/paper/skip', json={'candidate_id': first['candidate_id']})
    assert skipped['candidate'] is None and skipped['remaining_marks'] == 10
    second = request(client, 'POST', root + '/paper/next')
    assert second['candidate']['stem'].startswith('Question 2')
    regenerated = request(client, 'POST', root + '/paper/regenerate', json={'candidate_id': second['candidate_id']})
    assert regenerated['candidate']['stem'].startswith('Question 3')
    assert client.post(root + '/paper/accept', json={'candidate_id': second['candidate_id']}).status_code == 409
    edit = {key: regenerated['candidate'][key] for key in ('stem', 'options', 'correct_answer', 'explanation')}
    edit['stem'] = 'Professor modified question about algorithms?'
    candidate = request(client, 'PATCH', root + '/paper/candidate', json={'candidate_id': regenerated['candidate_id'], **edit})
    assert candidate['candidate']['grounded'] is False
    accepted = request(client, 'POST', root + '/paper/accept', json={'candidate_id': candidate['candidate_id']})
    assert len(accepted['questions']) == 1 and accepted['accepted_marks'] == 5
    assert accepted['candidate'] is None
    assert len(request(client, 'POST', root + '/paper/accept', json={'candidate_id': candidate['candidate_id']})['questions']) == 1
    question_id = accepted['questions'][0]['id']
    edit['stem'] = 'Edited persisted question with new details?'
    accepted = request(client, 'PATCH', root + f'/paper/questions/{question_id}', json=edit)
    assert accepted['questions'][0]['version'] == 2
    assert [item['version'] for item in request(client, 'GET', root + f'/paper/questions/{question_id}/versions')] == [2, 1]
    other = request(client, 'POST', root + '/paper/next')
    done = request(client, 'POST', root + '/paper/accept', json={'candidate_id': other['candidate_id']})
    assert done['status'] == 'complete' and done['accepted_marks'] == 10
    ids = [q['id'] for q in done['questions']]
    reordered = request(client, 'PUT', root + '/paper/order', json={'question_ids': ids[::-1]})
    assert [q['id'] for q in reordered['questions']] == ids[::-1]
    assert client.put(root + '/paper/order', json={'question_ids': [ids[0], ids[0]]}).status_code == 422
    assert request(client, 'GET', root + '/paper')['accepted_marks'] == 10
    reopened = request(client, 'DELETE', root + f'/paper/questions/{ids[0]}')
    assert reopened['status'] == 'ready' and reopened['remaining_marks'] == 5
    assert len(reopened['questions']) == 1


def test_configuration_changes_invalidate_blueprint_and_candidate(client):
    root = setup(client)
    request(client, 'POST', root + '/paper/prepare')
    request(client, 'POST', root + '/paper/approve')
    changed = request(client, 'PATCH', root + '/requirements', json={'updates': {'difficulty': 'hard++'}})
    assert changed['status'] == 'awaiting_approval'
    assert request(client, 'GET', root + '/paper')['stale'] is True
    assert client.post(root + '/paper/next').status_code == 409
    request(client, 'POST', root + '/approve')
    assert request(client, 'POST', root + '/paper/prepare')['status'] == 'awaiting_approval'


def test_invalid_edits_and_nonmatching_document_scope(client):
    root = setup(client)
    request(client, 'POST', root + '/paper/prepare')
    request(client, 'POST', root + '/paper/approve')
    assert client.post(root + '/paper/accept', json={'candidate_id':'missing-candidate'}).status_code == 409
    assert client.put(root + '/paper/order', json={'question_ids':['unknown']}).status_code == 422
