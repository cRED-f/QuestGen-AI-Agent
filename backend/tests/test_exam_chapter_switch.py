"""Professor chapter changes must reach paper-generation, not requirements parsing."""
import pytest
from fastapi.testclient import TestClient
from backend import app as service, exam_agent, generation


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'chapter.sqlite'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.delenv('QUESTGEN_EMBED_MODEL', raising=False)
    monkeypatch.setattr(service, 'model_credentials', lambda settings: ('http://fake/', {}))
    with TestClient(service.app) as c:
        with service.database() as conn:
            conn.execute("UPDATE workspace_settings SET model='test-model' WHERE id=1")
        yield c


def make_file(course, filename, content):
    doc, chunk = service.identifier(), service.identifier()
    with service.database() as conn:
        conn.execute('''INSERT INTO documents
          (id,course_id,filename,extension,sha256,bytes,status,progress,chunk_count,created_at,updated_at)
          VALUES (?,?,?,?,?,100,'ready',100,1,?,?)''',
          (doc, course, filename, '.pptx', service.identifier(), service.now(), service.now()))
        conn.execute('''INSERT INTO document_chunks(id,document_id,course_id,page,section,ordinal,content)
          VALUES (?,?,?,?,?,0,?)''', (chunk, doc, course, 1, filename, content))
        conn.execute('INSERT INTO chunks_fts(rowid,content) SELECT rowid,content FROM document_chunks WHERE id=?', (chunk,))
    return doc


def setup(client, scope='all_course', selected=None):
    course = client.get('/api/courses').json()[0]['id']
    first = make_file(course, 'Chapter 02 PPT.pptx', 'Economics supply demand pricing equilibrium analysis')
    second = make_file(course, 'Chapter 03 PPT.pptx', 'Economics elasticity productivity income substitution')
    thread = client.post('/api/threads', json={'course_id': course}).json()['id']
    root = f'/api/threads/{thread}/exam'
    updates = dict(faculty_initials='FI', university_name='Test University', exam_type='midterm',
                   total_marks=3, difficulty='moderate', question_formats=[{'kind':'mcq','count':3,'marks_each':1}],
                   material_scope=scope)
    payload = {'updates':updates}
    if scope == 'selected':
        payload['document_ids'] = selected or [first]
    assert client.patch(root+'/requirements',json=payload).status_code == 200
    assert client.post(root+'/approve').status_code == 200
    prepared = client.post(root+'/paper/prepare').json()
    blueprint = prepared['blueprint']
    blueprint['sections'][0]['topic'] = 'Chapter 02 PPT.pptx'
    assert client.put(root+'/paper/blueprint', json={'blueprint': blueprint}).status_code == 200
    assert client.post(root+'/paper/approve').status_code == 200
    return root, first, second


def generate_mock(monkeypatch, calls):
    async def fake_gen(settings, plan, sources, previous_issue='', *, on_reasoning=None):
        calls.append((plan.topic, [s['filename'] for s in sources], previous_issue))
        return generation.GeneratedQuestion(
            stem=f"What is a concept in {plan.topic} question {len(calls)}?",
            options=['A', 'B', 'C', 'D'], correct_answer='B', explanation='Because B applies.',
            source_ids=[sources[0]['chunk_id']] if sources else [])
    monkeypatch.setattr(generation, 'generate_question', fake_gen)


def test_chapter_switch_replaces_candidate_and_preserves_accepted(client, monkeypatch):
    root, first, second = setup(client)
    calls = []
    generate_mock(monkeypatch, calls)
    first_candidate = client.post(root+'/paper/next').json()
    assert first_candidate['candidate']['topic'] == 'Chapter 02 PPT.pptx'
    accepted = client.post(root+'/paper/accept',json={'candidate_id':first_candidate['candidate_id']}).json()
    assert len(accepted['questions']) == 1
    old = client.post(root+'/paper/next').json()
    assert 'Chapter 02' in old['candidate']['stem']

    response = client.post(root+'/turn', json={'content':'I want next question from chap 3'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert 'Chapter 03 PPT.pptx' in data['reply']
    paper = data['paper']
    assert len(paper['questions']) == 1
    assert paper['candidate_id'] != old['candidate_id']
    assert paper['candidate']['topic'] == 'Chapter 03 PPT.pptx'
    assert paper['candidate']['sources'] and {s['document_id'] for s in paper['candidate']['sources']} == {second}
    assert calls[-1][1] == ['Chapter 03 PPT.pptx']
    assert client.post(root+'/paper/accept', json={'candidate_id':old['candidate_id']}).status_code == 409
    accepted = client.post(root+'/paper/accept',json={'candidate_id':paper['candidate_id']}).json()
    assert accepted['questions'][0]['content']['topic'] == 'Chapter 02 PPT.pptx'
    assert accepted['questions'][1]['content']['topic'] == 'Chapter 03 PPT.pptx'
    assert accepted['accepted_marks'] == 2
    assert accepted['next_source'] is None
    assert accepted['questions'][1]['content']['sources'][0]['document_id'] == second
    assert client.get(root+'/paper').json()['questions'][1]['content']['topic'] == 'Chapter 03 PPT.pptx'


def test_unapproved_chapter_cannot_replace_candidate(client, monkeypatch):
    root, first, second = setup(client, scope='selected')
    calls = []
    generate_mock(monkeypatch, calls)
    candidate = client.post(root+'/paper/next').json()
    response = client.post(root+'/turn', json={'content':'I want next question from chap 3'})
    assert response.status_code == 200
    assert 'outside the approved material selection' in response.json()['reply']
    assert response.json()['paper']['candidate_id'] == candidate['candidate_id']
    assert len(calls) == 1


def test_unknown_chapter_fails_clearly_without_fallback(client, monkeypatch):
    root, first, second = setup(client)
    calls = []
    generate_mock(monkeypatch, calls)
    result = client.post(root+'/turn', json={'content':'Please generate the next question from chapter 99'})
    assert result.status_code == 200
    assert 'could not find a ready file' in result.json()['reply']
    assert result.json()['paper']['next_source'] is None
    assert calls == []


def test_failed_generation_keeps_chapter_for_retry_across_restart(client, monkeypatch):
    root, first, second = setup(client)
    async def timeout_gen(*args, **kwargs):
        raise ValueError('Temporary failure')
    monkeypatch.setattr(generation, 'generate_question', timeout_gen)
    response = client.post(root+'/turn', json={'content':'Generate next question from chapter 3'})
    assert response.status_code == 200
    assert response.json()['paper']['next_source']['topic'] == 'Chapter 03 PPT.pptx'
    service.init_db()  # restart must retain the approved per-slot source
    assert client.get(root+'/paper').json()['next_source']['document_ids'] == [second]
    calls = []
    generate_mock(monkeypatch, calls)
    candidate = client.post(root+'/paper/next')
    assert candidate.status_code == 200
    assert calls[-1][1] == ['Chapter 03 PPT.pptx']


def test_not_a_command_does_not_mutate_paper(client):
    root, first, second = setup(client)
    assert exam_agent.next_chapter_requested('Explain what is taught in chapter 3') is False
    assert exam_agent.next_chapter_requested('I want next question from chap 3') is True
    assert exam_agent.next_chapter_requested('Generate a new question from chapter 03') is True
