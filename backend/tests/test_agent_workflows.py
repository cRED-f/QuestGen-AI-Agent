"""Agent proposals are gated by real professor approval and existing paper services.

All model interactions are mocked; these tests do not claim to verify live LLMs.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from backend import app as service, exam_builder
from backend.agent import academic, state, workflows


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'workflows.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as client:
        cid = client.get('/api/courses').json()[0]['id']
        tid = client.post('/api/threads', json={'course_id':cid}).json()['id']
        with service.database() as conn:
            conn.execute('''INSERT INTO documents(id,course_id,filename,extension,bytes,sha256,status,created_at,updated_at)
                            VALUES (?,?,?,'.txt',100,'hash','ready',?,?)''',
                         ('doc-a', cid, 'Economics.txt', service.now(), service.now()))
            conn.execute('INSERT INTO agent_sessions(thread_id,document_ids,scope_locked,updated_at) VALUES (?,?,1,?)',
                         (tid, '["doc-a"]', service.now()))
            entry = academic._empty(cid, ['doc-a'])
            entry['topics'] = [{'id':'topic-a','title':'Opportunity Cost', 'evidence':[
                {'chunk_id':'chunk-a','document_id':'doc-a','filename':'Economics.txt','page':1,'section':'Intro'}]},
                {'id':'topic-b','title':'Comparative Advantage', 'evidence':[
                {'chunk_id':'chunk-b','document_id':'doc-a','filename':'Economics.txt','page':2,'section':'Trade'}]}]
            academic._save(conn, tid, entry)
        yield client, cid, tid


def exam_plan():
    return {'kind':'exam','title':'Economics Midterm','rationale':'These two lecture topics test quantitative and conceptual understanding.',
        'exam_type':'midterm','sections':[{'topic_id':'topic-a','kind':'mcq','count':2,'marks_each':2.5},
                                         {'topic_id':'topic-b','kind':'short_answer','count':1,'marks_each':5.0}]}


def test_proposal_approval_reuses_exam_pipeline_without_generating(workspace):
    client, cid, tid = workspace
    with service.database() as conn:
        proposed = workflows.propose(conn, tid, cid, ['doc-a'], exam_plan())
        assert proposed['status'] == 'proposed'
        assert exam_builder.read(conn, tid) is None
    service.init_db()  # schema reinitialization must not overwrite an undetermined plan
    assert client.get(f'/api/threads/{tid}/agent/workflow').json()['id'] == proposed['id']
    result = client.post(f'/api/threads/{tid}/agent/workflow/{proposed["id"]}/decision',json={'approve': True})
    assert result.status_code == 200, result.text
    content = result.json()
    assert content['workflow']['status'] == 'approved'
    assert content['paper']['status'] == 'awaiting_approval'
    assert content['paper']['questions'] == [] and content['paper']['drafts'] == []
    assert content['paper']['total_questions'] == 3
    assert content['paper']['total_marks'] == 10
    assert [x['topic'] for x in content['paper']['blueprint']['sections']] == ['Opportunity Cost','Comparative Advantage']
    assert content['exam_session']['requirements']['material_scope'] == 'selected'
    assert content['exam_session']['document_ids'] == ['doc-a']
    assert content['paper']['material_ids'] == ['doc-a']
    assert client.post(f'/api/threads/{tid}/agent/workflow/{proposed["id"]}/decision',json={'approve':True}).status_code == 409
    # The original exam approval endpoint remains the only way to authorize generation.
    assert client.post(f'/api/threads/{tid}/exam/paper/approve').status_code == 200
    assert client.get(f'/api/threads/{tid}/exam/paper').json()['status'] == 'ready'


def test_plan_rejected_and_revision_proposed_without_mutating_exam(workspace):
    client, cid, tid = workspace
    with service.database() as conn:
        a = workflows.propose(conn, tid, cid, ['doc-a'], exam_plan())
        edited = {**exam_plan(), 'title': 'Economics Test', 'rationale':'Revision shifts focus while retaining taught content.'}
        b = workflows.propose(conn, tid, cid, ['doc-a'], edited)
        assert b['id'] != a['id']
    url = f'/api/threads/{tid}/agent/workflow/'
    assert client.post(url+a['id']+'/decision',json={'approve':True}).status_code == 409
    declined = client.post(url+b['id']+'/decision',json={'approve':False})
    assert declined.status_code == 200
    assert declined.json()['workflow']['status'] == 'rejected'
    assert client.get(f'/api/threads/{tid}/exam/paper').json() is None


def test_invalid_topics_and_unselected_sources_cannot_be_proposed(workspace):
    _, cid, tid = workspace
    with service.database() as conn:
        with pytest.raises(ValueError, match='not supported'):
            workflows.propose(conn, tid, cid, ['doc-a'], {**exam_plan(),
                'sections':[{'topic_id':'fabricated','kind':'mcq','count':2,'marks_each':2.5}]})
        with pytest.raises(ValueError, match='chunk outside'):
            workflows.propose(conn, tid, cid, ['doc-a'], {'kind':'research_summary',
                'title':'Research Summary','rationale':'A supported summary of lectures and examples.',
                'content':'An explanation with wrong citations.','course_chunk_ids':['foreign']})
        assert workflows.latest(conn, tid) is None


def test_research_artifact_approval_and_scope_are_persisted(workspace):
    client, cid, tid = workspace
    with service.database() as conn:
        plan = workflows.propose(conn, tid, cid, ['doc-a'], {'kind':'topic_coverage',
            'title':'Concept Map Overview','rationale':'Compare the two observed concepts and their instructional relationships.',
            'content':'**Opportunity cost** and comparative advantage are connected.',
            'course_chunk_ids':['chunk-a','chunk-b']})
    response = client.post(f'/api/threads/{tid}/agent/workflow/{plan["id"]}/decision',json={'approve':True})
    assert response.status_code == 200, response.text
    assert 'Opportunity cost' in response.json()['workflow']['result']['content']
    assert client.get(f'/api/threads/{tid}/exam/paper').json() is None
    with service.database() as conn:
        conn.execute("UPDATE agent_sessions SET document_ids='[]' WHERE thread_id=?", (tid,))
        newer = workflows.propose(conn, tid, cid, ['doc-a'], exam_plan())
    response = client.post(f'/api/threads/{tid}/agent/workflow/{newer["id"]}/decision',json={'approve':True})
    assert response.status_code == 409
