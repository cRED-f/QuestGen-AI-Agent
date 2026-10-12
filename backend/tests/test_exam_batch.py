"""New contract: context-engineered mixed question batch, selective revision and approval."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from backend import app as service, generation, exam_batch, exam_state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTGEN_DB_PATH',str(tmp_path/'batch.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR',str(tmp_path/'uploads'))
    with TestClient(service.app) as api:
        yield api


def setup(client, monkeypatch):
    course_id = client.get('/api/courses').json()[0]['id']
    tid = client.post('/api/threads',json={'course_id':course_id}).json()['id']
    base = f'/api/threads/{tid}/exam'
    response = client.patch(base+'/requirements',json={'updates':{
        'exam_type':'midterm','total_marks':15,
        'question_formats':[{'kind':'mcq','count':2,'marks_each':2},
                            {'kind':'short_answer','count':1,'marks_each':3},
                            {'kind':'problem_solving','count':1,'marks_each':8}],
        'material_scope':'general'}})
    assert response.status_code==200,response.text
    assert response.json()['ready_for_approval'] is True
    assert response.json()['requirements'].get('difficulty') is None
    assert client.post(base+'/approve').status_code==200
    blueprint = client.post(base+'/paper/prepare').json()['blueprint']
    for section in blueprint['sections']:
        section['topic']='Graph traversal and shortest path algorithms'
        assert section['difficulty']=='auto'
    assert client.put(base+'/paper/blueprint',json={'blueprint':blueprint}).status_code==200
    assert client.post(base+'/paper/approve').status_code==200
    monkeypatch.setattr(service,'model_credentials',lambda settings:('http://mock/',{}))
    with service.database() as conn:
        conn.execute("UPDATE workspace_settings SET model='mock' WHERE id=1")
    return base+'/paper'


def wait(client,base,rid):
    for _ in range(150):
        result=client.get(base+'/generation/'+rid).json()
        if result['status'] not in ('queued','running'):
            return result
        time.sleep(.02)
    pytest.fail('request did not complete')


def model_response(request, suffix=''):
    questions=[]
    for slot in request['slots']:
        n=slot['ordinal'];kind=slot['kind']
        questions.append({'slot':n,'stem':f'Question {n}: explain graph traversal {suffix} and its implications?',
                          'options':['Queue','Stack','Heap','Array'] if kind=='mcq' else [],
                          'correct_answer':'Queue' if kind=='mcq' else 'A correct graph traversal explanation.',
                          'explanation':'Explain how algorithm traversal works and justify the reasoning.',
                          'source_ids':[]})
    return {'questions':questions}


def test_batch_mixed_formats_inferred_complexity_and_selective_revision(client,monkeypatch):
    base=setup(client,monkeypatch)
    calls=[]
    async def fake(settings,system,request,*,max_tokens,on_reasoning=None):
        calls.append(request)
        return model_response(request, 'revised' if len(calls)>1 else 'initial')
    monkeypatch.setattr(generation,'model_json',fake)
    start=client.post(base+'/generation/start',json={'mode':'batch'})
    assert start.status_code==202,start.text
    result=wait(client,base,start.json()['request_id'])
    assert result['status']=='succeeded',result
    drafts=result['paper']['drafts']; assert len(drafts)==4
    assert len(calls)==1 # one writer request for all mixed types
    assert {d['content']['kind'] for d in drafts}=={'mcq','short_answer','problem_solving'}
    assert all(d['content']['difficulty']!='auto' for d in drafts)
    assert all(d['content']['bloom_level'] for d in drafts)
    kept=drafts[:3]; flagged=drafts[3]
    for draft in kept:
        status=client.put(base+'/drafts/decision',json={'draft_id':draft['id'],'decision':'keep','version':draft['version']})
        assert status.status_code==200,status.text
    assert client.put(base+'/drafts/decision',json={'draft_id':flagged['id'],'decision':'revise','version':flagged['version']}).status_code==200
    assert client.post(base+'/drafts/approve',json={'draft_ids':[d['id'] for d in kept], 'expected_versions':{d['id']:d['version'] for d in kept}}).status_code==200
    before=client.get(base).json(); assert len(before['questions'])==3
    unchanged={q['id']:(q['version'],q['content']) for q in before['questions']}
    generation_start=client.post(base+'/generation/start',json={'mode':'revision','draft_ids':[flagged['id']],
                                                                 'instructions':'Increase analytical challenge'})
    result=wait(client,base,generation_start.json()['request_id'])
    assert result['status']=='succeeded',result
    assert len(calls)==2 and len(calls[1]['slots'])==1
    after=result['paper']; assert len(after['questions'])==3
    assert {q['id']:(q['version'],q['content']) for q in after['questions']}==unchanged
    revised=after['drafts'][0];assert revised['id']==flagged['id'] and revised['version']==2
    assert revised['decision']=='pending'
    assert 'revised' in revised['content']['stem']
    assert client.post(base+'/drafts/approve',json={'draft_ids':[revised['id']], 'expected_versions':{revised['id']:revised['version']}}).status_code==409
    assert client.put(base+'/drafts/decision',json={'draft_id':revised['id'],'decision':'keep','version':2}).status_code==200
    done=client.post(base+'/drafts/approve',json={'draft_ids':[revised['id']], 'expected_versions':{revised['id']:revised['version']}})
    assert done.status_code==200,done.text
    assert done.json()['status']=='complete' and len(done.json()['questions'])==4
    assert done.json()['drafts']==[]


def test_selected_draft_revision_can_be_cleared_without_marking_it_kept(client, monkeypatch):
    base = setup(client, monkeypatch)
    async def fake(settings, system, request, *, max_tokens, on_reasoning=None):
        return model_response(request)
    monkeypatch.setattr(generation, 'model_json', fake)
    response = client.post(base + '/generation/start', json={'mode': 'batch'})
    result = wait(client, base, response.json()['request_id'])
    assert result['status'] == 'succeeded'
    draft = result['paper']['drafts'][0]
    choice = {'draft_id': draft['id'], 'version': draft['version']}
    flagged = client.put(base + '/drafts/decision', json={**choice, 'decision': 'revise'})
    assert flagged.status_code == 200
    assert next(d for d in flagged.json()['drafts'] if d['id'] == draft['id'])['decision'] == 'revise'
    cleared = client.put(base + '/drafts/decision', json={**choice, 'decision': 'pending'})
    assert cleared.status_code == 200
    cleared_draft = next(d for d in cleared.json()['drafts'] if d['id'] == draft['id'])
    assert cleared_draft['decision'] == 'pending'
    assert cleared_draft['version'] == draft['version']
    # A cleared selection cannot be revised unless explicitly re-flagged.
    rejected = client.post(base + '/generation/start', json={
        'mode': 'revision', 'draft_ids': [draft['id']], 'instructions': 'More analytical',
    })
    assert rejected.status_code == 409


def test_failed_large_output_falls_back_to_sub_batched_generation(client,monkeypatch):
    base=setup(client,monkeypatch)
    sizes=[]
    async def model(settings,system,request,*,max_tokens,on_reasoning=None):
        sizes.append(len(request['slots']))
        if len(request['slots'])>2:
            raise ValueError('Model exhausted its output token limit before returning JSON')
        return model_response(request)
    monkeypatch.setattr(generation,'model_json',model)
    result=client.post(base+'/generation/start',json={'mode':'batch'})
    status=wait(client,base,result.json()['request_id'])
    assert status['status']=='succeeded',status
    assert sizes==[4,2,2]
    assert len(status['paper']['drafts'])==4


def test_stale_versions_and_selected_revision_scope(client,monkeypatch):
    base=setup(client,monkeypatch)
    async def model(settings,system,request,*,max_tokens,on_reasoning=None):return model_response(request)
    monkeypatch.setattr(generation,'model_json',model)
    start=client.post(base+'/generation/start',json={'mode':'batch'})
    paper=wait(client,base,start.json()['request_id'])['paper']
    draft=paper['drafts'][0]
    assert client.post(base+'/generation/start',json={'mode':'revision','draft_ids':[draft['id']]}).status_code==409
    assert client.put(base+'/drafts/decision',json={'draft_id':draft['id'],'decision':'keep','version':99}).status_code==409
    assert client.post(base+'/drafts/approve',json={'draft_ids':[draft['id']], 'expected_versions':{draft['id']:99}}).status_code==409
    assert client.put(base+'/drafts/decision',json={'draft_id':draft['id'],'decision':'keep','version':1}).status_code==200
    assert client.put(base+'/drafts/decision',json={'draft_id':draft['id'],'decision':'revise','version':1}).status_code==200
    assert client.post(base+'/generation/start',json={'mode':'revision','draft_ids':['outside-id']}).status_code==409


def test_failed_later_group_preserves_valid_drafts_and_resume(client,monkeypatch):
    from backend import exam_batch
    base=setup(client,monkeypatch)
    old_budget=exam_batch._budget
    monkeypatch.setattr(exam_batch,'_budget',lambda slots: 13000 if len(slots)>2 else 1200)
    calls=[]
    async def model(settings,system,request,*,max_tokens,on_reasoning=None):
        calls.append([s['ordinal'] for s in request['slots']])
        if request['slots'][0]['ordinal']>2:
            raise ValueError('Model returned invalid JSON')
        return model_response(request)
    monkeypatch.setattr(generation,'model_json',model)
    start=client.post(base+'/generation/start',json={'mode':'batch'})
    status=wait(client,base,start.json()['request_id'])
    assert status['status']=='failed'
    saved=status['paper']['drafts']
    assert [d['slot'] for d in saved]==[1,2]
    saved_ids=[d['id'] for d in saved]
    async def recovered(settings,system,request,*,max_tokens,on_reasoning=None):return model_response(request)
    monkeypatch.setattr(generation,'model_json',recovered)
    result=client.post(base+'/generation/start',json={'mode':'batch'})
    status=wait(client,base,result.json()['request_id'])
    assert status['status']=='succeeded',status
    assert len(status['paper']['drafts'])==4
    assert [d['id'] for d in status['paper']['drafts'][:2]]==saved_ids
    monkeypatch.setattr(exam_batch,'_budget',old_budget)


def test_batch_selected_materials_only_accepts_approved_source_ids(client,monkeypatch):
    course_id=client.get('/api/courses').json()[0]['id']
    tid=client.post('/api/threads',json={'course_id':course_id}).json()['id']
    root=f'/api/threads/{tid}/exam'
    uploaded=client.post(f'/api/courses/{course_id}/documents',files={
        'file':('chapter1.txt',b'Breadth-first search uses a FIFO queue. '
              b'Depth-first search uses a stack. Dijkstra shortest path uses priority queues.')})
    assert uploaded.status_code==201,uploaded.text
    doc=uploaded.json()['id']
    for _ in range(100):
        status=client.get(f'/api/courses/{course_id}/documents/{doc}').json()['status']
        if status=='ready':break
        time.sleep(.02)
    assert status=='ready'
    config={'updates':{'exam_type':'quiz','total_marks':2,
           'question_formats':[{'kind':'short_answer','count':1,'marks_each':2}],
           'material_scope':'selected'}, 'document_ids':[doc]}
    assert client.patch(root+'/requirements',json=config).status_code==200
    assert client.post(root+'/approve').status_code==200
    assert client.post(root+'/paper/prepare').status_code==200
    assert client.post(root+'/paper/approve').status_code==200
    monkeypatch.setattr(service,'model_credentials',lambda settings:('http://mock/',{}))
    with service.database() as conn:conn.execute("UPDATE workspace_settings SET model='mock' WHERE id=1")
    seen=[]
    async def valid(settings,system,request,*,max_tokens,on_reasoning=None):
        ids=request['slots'][0]['allowed_evidence_ids']
        seen.extend(ids)
        assert ids and all(e['chunk_id'] in ids for e in request['source_library'])
        return {'questions':[{'slot':1,'stem':'Explain how BFS traversal operates using the queue data structure.',
          'options':[],'correct_answer':'It uses a FIFO queue to explore graph vertices.',
          'explanation':'The lecture explicitly describes breadth-first traversal with FIFO.',
          'source_ids':[ids[0]]}]}
    monkeypatch.setattr(generation,'model_json',valid)
    base=root+'/paper'
    task=client.post(base+'/generation/start',json={'mode':'batch'})
    assert task.status_code==202,task.text
    completed=wait(client,base,task.json()['request_id'])
    assert completed['status']=='succeeded',completed
    result=completed['paper']['drafts'][0]['content']
    assert result['grounded'] and result['source_ids']==[seen[0]]
    assert result['sources'][0]['chunk_id']==seen[0]


def test_final_export_without_optional_university_or_difficulty(client,monkeypatch):
    base=setup(client,monkeypatch)
    async def model(settings,system,request,*,max_tokens,on_reasoning=None):return model_response(request)
    monkeypatch.setattr(generation,'model_json',model)
    started=client.post(base+'/generation/start',json={'mode':'batch'})
    result=wait(client,base,started.json()['request_id'])
    assert result['status']=='succeeded',result
    expected_versions={d['id']:d['version'] for d in result['paper']['drafts']}
    for draft in result['paper']['drafts']:
        response=client.put(base+'/drafts/decision',json={
            'draft_id':draft['id'],'version':draft['version'],'decision':'keep'})
        assert response.status_code==200,response.text
    approved=client.post(base+'/drafts/approve',json={
        'draft_ids':list(expected_versions),'expected_versions':expected_versions})
    assert approved.status_code==200,approved.text
    assert approved.json()['status']=='complete'
    final=client.post(base+'/final-approve')
    assert final.status_code==200,final.text
    exported=client.get(base+'/export/instructor.docx')
    assert exported.status_code==200,exported.text[:150]
    assert exported.content.startswith(b'PK')


def test_budget_floor_and_truncation_escalation(monkeypatch):
    """Thinking models need reasoning tokens; small batches escalate before failing."""
    single_slot=[{'ordinal':1,'kind':'mcq','marks':2,'topic':'Graph traversal','difficulty':'moderate'}]
    assert exam_batch._budget(single_slot) >= 4096
    attempts=[]
    async def fake(settings,system,request,*,max_tokens,on_reasoning=None):
        attempts.append(max_tokens)
        if len(attempts)==1:
            raise ValueError('Model exhausted its output token limit before returning JSON')
        return {'questions':[{'slot':1,
                 'stem':'Which graph traversal visits nodes level by level?',
                 'options':['Breadth-first search','Depth-first search','Prim','Kruskal'],
                 'correct_answer':'Breadth-first search',
                 'explanation':'It expands the frontier one level at a time using a queue.',
                 'source_ids':[]}]}
    monkeypatch.setattr(generation,'model_json',fake)
    slot={'ordinal':1,'kind':'mcq','marks':2,'topic':'Graph traversal',
          'difficulty':'moderate','bloom_level':'understand','section_id':'sec-1',
          'evidence':[]}
    items=asyncio.run(exam_batch._generate_recursive(
        {'provider':'ollama','model':'m'},[slot],'general','',[]))
    assert [item['ordinal'] for item in items]==[1]
    assert attempts==[4096,8192]
