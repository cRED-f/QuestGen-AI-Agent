"""Source-backed topic extraction, durable priorities and adaptive consultation.

Fake models exercise application contracts; no test claims live model quality.
"""
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend import app as service
from backend.agent import academic, consultation


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'academic.sqlite3'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def setup(client):
    course = client.get('/api/courses').json()[0]['id']
    thread = client.post('/api/threads', json={'course_id': course}).json()['id']
    with service.database() as conn:
        for doc_id, filename in [('a', 'Course One.txt'), ('b', 'Course Two.txt'), ('excluded', 'Other.txt')]:
            conn.execute('''INSERT INTO documents
                (id,course_id,filename,extension,bytes,sha256,status,created_at,updated_at)
                VALUES (?,?,?,'.txt',1,?,'ready',?,?)''',
                (doc_id, course, filename, doc_id, service.now(), service.now()))
        chunks = [('a', 1, 'Concept A: networks have vertices and edges'),
                  ('a', 2, 'Concept B: path finding traverses graphs'),
                  ('b', 1, 'Concept A is used with path finding for routes'),
                  ('excluded', 1, 'SECRET EXCLUDED TOPIC: forbidden example')]
        for ordinal, (doc_id, page, content) in enumerate(chunks):
            conn.execute('''INSERT INTO document_chunks(id,course_id,document_id,page,section,ordinal,content)
                VALUES (?,?,?,?,?,?,?)''', (f'source-{ordinal}', course, doc_id, page, 'Topic', ordinal, content))
    return course, thread


@pytest.fixture
def fake_messages(monkeypatch):
    package = types.ModuleType('langchain_core')
    package.__path__ = []
    module = types.ModuleType('langchain_core.messages')
    module.SystemMessage = module.HumanMessage = lambda *, content: SimpleNamespace(content=content)
    monkeypatch.setitem(sys.modules, 'langchain_core', package)
    monkeypatch.setitem(sys.modules, 'langchain_core.messages', module)


class FakeModel:
    def __init__(self, results):
        self.results = results
        self.calls = []
        self.bound = {}

    def bind(self, **kwargs):
        self.bound.update(kwargs)
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=json.dumps(self.results.pop(0)))


@pytest.mark.asyncio
async def test_topic_graph_reads_all_selected_chunks_incrementally(client, fake_messages):
    course, thread = setup(client)
    model = FakeModel([
        {'topics': [{'title': 'Graph structures', 'description': 'Network nodes and edges',
                     'subtopics': ['Vertices', 'Edges'],
                     'learning_objectives': ['Identify vertices in a network'],
                     'assessment_opportunities': ['Interpret a diagram'],
                     'cognitive_demand': {'levels': ['understand'], 'rationale': 'Describe elements'},
                     'evidence_ids': ['source-0']}], 'relationships': []},
        {'topics': [{'title': 'Path finding', 'description': 'Traversal',
                     'subtopics': ['Paths'], 'learning_objectives': [],
                     'assessment_opportunities': ['Analyze route selection'],
                     'cognitive_demand': {'levels': ['apply'], 'rationale': 'Use a method'},
                     'evidence_ids': ['source-1', 'source-2']}],
         'relationships': [{'from': 'Graph structures', 'to': 'Path finding',
                            'kind': 'prerequisite', 'rationale': 'Paths use graphs',
                            'evidence_ids': ['source-1']}]},
    ])
    first = await academic.analyze(thread, course, ['a', 'b'], model, batch_size=1)
    assert first['coverage']['analyzed_chunks'] == 1
    assert not first['coverage']['complete']
    assert first['topics'][0]['evidence'][0]['chunk_id'] == 'source-0'
    second = await academic.analyze(thread, course, ['a', 'b'], model, batch_size=2)
    assert second['coverage']['analyzed_chunks'] == second['coverage']['indexed_chunks'] == 3
    assert second['coverage']['complete']
    assert len(second['topics']) == 2
    assert second['relationships'][0]['kind'] == 'prerequisite'
    assert all('SECRET' not in m.content for call in model.calls for m in call)
    service.init_db()
    saved = academic.snapshot(thread, course, ['a', 'b'])
    assert len(saved['relationships']) == 1
    assert saved['coverage']['complete']
    # A completed scan performs no new model calls.
    assert await academic.analyze(thread, course, ['a', 'b'], model) == saved


def test_merge_rejects_fabricated_citations_and_relations():
    original = {'topics': [], 'relationships': [], 'offsets': {}}
    batch = [{'chunk_id': 'real', 'document_id': 'allowed', 'filename': 'Lecture',
              'page': 2, 'section': 'one'}]
    result = academic.merge(original, {
        'topics': [{'title': 'Invalid', 'evidence_ids': ['fabricated']},
                   {'title': 'Supported', 'evidence_ids': ['real']}],
        'relationships': [{'from': 'Invalid', 'to': 'Supported',
                           'kind': 'fake', 'evidence_ids': ['real']}],
    }, batch)
    assert [topic['title'] for topic in result['topics']] == ['Supported']
    assert result['relationships'] == []


def test_recording_professor_decisions_persists_and_deduplicates(client):
    course, thread = setup(client)
    with service.database() as conn:
        entry = academic._empty(course, ['a'])
        entry['topics'] = [{'id': 'topic-ok', 'title': 'Algorithms', 'evidence': []}]
        academic._save(conn, thread, entry)
    for _ in range(2):
        outcome = academic.record_answer(thread, course, ['a'], question='Explore algorithms?',
                                         answer='Prioritize applications', context='Two supported choices',
                                         topic_ids=['topic-ok', 'invented'])
        assert outcome['topic_ids'] == ['topic-ok']
    view = academic.snapshot(thread, course, ['a'])
    assert len(view['decisions']) == 1
    assert view['decisions'][0]['answer'] == 'Prioritize applications'
    assert view['topics'][0]['professor_guidance'][0]['answer'] == 'Prioritize applications'
    assert view['research_plan']['topics_to_reconsider'] == ['topic-ok']
    plan = academic.set_research_focus(thread, course, ['a'], goal='Focus on case studies',
                                       topic_ids=['topic-ok', 'fake'], rationale='Professor choice')
    assert plan['focus_topic_ids'] == ['topic-ok']
    assert academic.snapshot(thread, course, ['a'])['research_plan']['goal'] == 'Focus on case studies'
    with pytest.raises(ValueError, match='another course or document'):
        academic.snapshot(thread, course, ['b'])


@pytest.mark.asyncio
async def test_consultation_generates_contextual_question_and_adapts(client, fake_messages):
    course, thread = setup(client)
    with service.database() as conn:
        entry = academic._empty(course, ['a'])
        entry['topics'] = [{'id': 'subject-a', 'title': 'Path finding', 'evidence': [],
                             'cognitive_demand': {'levels': ['apply'], 'rationale': 'Problems'}}]
        academic._save(conn, thread, entry)
    question = {'needs_input': True, 'question': 'Compare shortest paths or traversal?',
                'context': 'Two task styles have different goals.', 'topic_ids': ['subject-a', 'wrong'],
                'options': ['Shortest paths', 'Traversal'], 'reason': 'Consequence',
                'recommended_default': 'Both'}
    model = FakeModel([question, {'needs_input': False, 'question': '',
                                   'reason': 'Professor has chosen'}])
    first = await consultation.suggest(thread, course, ['a'], model, 'Create analytical tasks')
    assert first['needs_input'] and first['topic_ids'] == ['subject-a']
    academic.record_answer(thread, course, ['a'], question=first['question'],
                           answer='Shortest paths', context=first['context'], topic_ids=first['topic_ids'])
    second = await consultation.suggest(thread, course, ['a'], model, 'Create analytical tasks')
    assert not second['needs_input']
    assert 'Shortest paths' in model.calls[1][1].content


@pytest.mark.asyncio
async def test_cognitive_demand_is_model_inferred_without_forcing_preferences(client, fake_messages):
    course, thread = setup(client)
    with service.database() as conn:
        entry = academic._empty(course, ['a'])
        entry['topics'] = [{'id': 'topic-path', 'title': 'Algorithms', 'evidence': []}]
        academic._save(conn, thread, entry)
    model = FakeModel([{'overview': 'Prefer applied tasks when the lectures support them',
                        'topic_recommendations': [
                            {'topic_id': 'topic-path', 'cognitive_actions': ['apply', 'analyze'],
                             'question_styles': ['worked scenarios'], 'reason': 'Examples in notes'},
                            {'topic_id': 'unknown', 'reason': 'Unsupported'}],
                        'assumptions': ['Student level not provided'], 'uncertainties': []}])
    output = await consultation.demand(thread, course, ['a'], model, 'Create a test')
    assert len(output['topic_recommendations']) == 1
    assert output['topic_recommendations'][0]['cognitive_actions'] == ['apply', 'analyze']
    assert output['assumptions'] == ['Student level not provided']


def test_analysis_endpoint_is_thread_scoped(client):
    course, thread = setup(client)
    response = client.get(f'/api/threads/{thread}/agent/analysis')
    assert response.status_code == 200
    assert response.json()['topics'] == []
    assert response.json()['coverage']['indexed_chunks'] == 0
    assert client.get('/api/threads/not-a-thread/agent/analysis').status_code == 404
