"""Contract: progressive HITL exam configuration and an approval gate."""
import pytest
from fastapi.testclient import TestClient
from backend import app as service, exam_agent


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('QUESTGEN_DB_PATH', str(tmp_path / 'exam.sqlite'))
    monkeypatch.setenv('QUESTGEN_UPLOAD_DIR', str(tmp_path / 'uploads'))
    with TestClient(service.app) as api:
        yield api


def make_thread(client, course_id=None):
    if course_id is None:
        course_id = client.get('/api/courses').json()[0]['id']
    return client.post('/api/threads', json={'course_id': course_id}).json()['id']


def complete(client, tid, scope='general', documents=None):
    base = f'/api/threads/{tid}/exam'
    details = {'faculty_initials': 'FI', 'university_name': 'Example University',
               'exam_type': 'midterm', 'total_marks': 40, 'difficulty': 'complex',
               'question_formats': [{'kind': 'mcq', 'count': 10, 'marks_each': 2},
                                    {'kind': 'long_answer', 'count': 4, 'marks_each': 5}],
               'material_scope': scope}
    payload = {'updates': details}
    if documents is not None:
        payload['document_ids'] = documents
    return client.patch(base + '/requirements', json=payload)


def test_exam_bootstrap_and_history_are_shared(client):
    tid = make_thread(client)
    with service.database() as conn:
        conn.execute("INSERT INTO messages(id,thread_id,role,content,created_at) VALUES (?,?, 'user',?,?)",
                     (service.identifier(), tid, 'Our graph algorithms midterm should cover shortest paths.', service.now()))
    session = client.post(f'/api/threads/{tid}/exam/start').json()
    assert session['status'] == 'collecting'
    assert session['missing'][0] == 'exam_type'
    assert 'graph algorithms' in session['context_summary']
    assert client.get(f'/api/threads/{tid}/exam').json()['context_summary'] == session['context_summary']
    assert client.get(f'/api/threads/{tid}/messages').json()[0]['mode'] == 'chat'
    assert client.post(f'/api/threads/{tid}/exam/start').json()['context_summary'] == session['context_summary']


def test_stepwise_exam_turn_survives_backend_reinitialization(client):
    tid = make_thread(client)
    client.post(f'/api/threads/{tid}/exam/start')
    result = client.post(f'/api/threads/{tid}/exam/turn', json={'content': 'midterm'})
    assert result.status_code == 200, result.text
    assert result.json()['session']['requirements']['exam_type'] == 'midterm'
    assert 'mark' in result.json()['reply'].lower()
    response = client.post(f'/api/threads/{tid}/exam/turn', json={'content': '40 marks'})
    assert response.json()['session']['requirements']['total_marks'] == 40
    assert 'question' in response.json()['reply'].lower()
    service.init_db()  # simulate schema initialization at restart
    restored = client.get(f'/api/threads/{tid}/exam').json()
    assert restored['requirements']['exam_type'] == 'midterm'
    assert restored['requirements']['total_marks'] == 40
    messages = client.get(f'/api/threads/{tid}/messages').json()
    assert [m['role'] for m in messages] == ['user', 'assistant', 'user', 'assistant']
    assert all(m['mode'] == 'exam' for m in messages)


def test_multi_field_model_tool_calls_and_no_repeat_questions(client, monkeypatch):
    tid = make_thread(client)
    client.post(f'/api/threads/{tid}/exam/start')
    async def proposed(message, session, settings, **kwargs):
        assert message == 'I am FI at Example University preparing a 40 mark midterm'
        return [('save_exam_requirements', {'faculty_initials': 'FI', 'university_name': 'Example University',
                                            'exam_type': 'midterm', 'total_marks': 40})]
    monkeypatch.setattr(exam_agent, 'propose_tool_calls', proposed)
    result = client.post(f'/api/threads/{tid}/exam/turn', json={
        'content': 'I am FI at Example University preparing a 40 mark midterm'})
    assert result.status_code == 200
    assert result.json()['session']['missing'][0] == 'question_formats'
    assert 'question' in result.json()['reply'].lower()
    with service.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exam_tool_events WHERE thread_id=? AND tool_name='save_exam_requirements'", (tid,)).fetchone()[0] == 1


def test_no_approval_if_incomplete_and_bulk_generation_blocked(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    assert client.post(base + '/approve').status_code == 404
    assert client.post(f'/api/threads/{tid}/generations', json={
        'prompt': 'Create some questions'}).status_code == 409
    client.post(base + '/start')
    assert client.post(base + '/approve').status_code == 409
    assert client.post(f'/api/threads/{tid}/generations', json={
        'prompt': 'Create some questions'}).status_code == 409
    assert client.get(f'/api/threads/{tid}/generations').json() == []


def test_approved_configuration_revoked_when_edited(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    saved = complete(client, tid)
    assert saved.status_code == 200, saved.text
    assert saved.json()['ready_for_approval'] is True
    assert saved.json()['status'] == 'awaiting_approval'
    assert client.post(base + '/approve').json()['status'] == 'approved'
    updated = client.patch(base + '/requirements', json={'updates': {'difficulty': 'hard++'}})
    assert updated.json()['requirements']['difficulty'] == 'hard++'
    assert updated.json()['status'] == 'awaiting_approval'
    assert updated.json()['approved_at'] is None
    assert client.post(f'/api/threads/{tid}/generations', json={
        'prompt': 'Generate the final paper'}).status_code == 409
    cleared = client.patch(base + '/requirements', json={'updates': {'university_name': None}}).json()
    assert 'university_name' not in cleared['missing']  # Institution is optional.
    assert client.post(base + '/approve').status_code == 200
    # Material requirements, unlike display metadata, remain mandatory.
    invalid = client.patch(base + '/requirements', json={'updates': {'total_marks': None}}).json()
    assert 'total_marks' in invalid['missing']
    assert client.post(base + '/approve').status_code == 409


def test_invalid_distribution_and_tool_args_cannot_approve(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    saved = complete(client, tid)
    assert saved.json()['ready_for_approval'] is True
    wrong = client.patch(base + '/requirements', json={'updates': {
        'question_formats': [{'kind': 'mcq', 'count': 3, 'marks_each': 2}]}}).json()
    assert 'marks_distribution' in wrong['missing']
    assert client.post(base + '/approve').status_code == 409
    assert client.patch(base + '/requirements', json={'updates': {'difficulty': 'impossible'}}).status_code == 422
    assert client.patch(base + '/requirements', json={'updates': {
        'question_formats': [{'kind': 'made_up', 'count': 1, 'marks_each': 40}]}}).status_code == 422


def test_selected_materials_course_scoped_and_required(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    saved = complete(client, tid, scope='selected')
    assert saved.status_code == 200
    assert 'document_ids' in saved.json()['missing']
    assert client.post(base + '/approve').status_code == 409
    invalid = client.patch(base + '/requirements', json={'updates': {}, 'document_ids': ['nonexistent']})
    assert invalid.status_code == 422
    assert client.get(base).json()['document_ids'] == []


def test_exam_delete_cascades_and_no_made_up_chat_memory(client):
    tid = make_thread(client)
    assert client.post(f'/api/threads/{tid}/exam/start').json()['context_summary'] == ''
    client.post(f'/api/threads/{tid}/exam/turn', json={'content': 'FI'})
    assert client.delete(f'/api/threads/{tid}').status_code == 204
    with service.database() as conn:
        assert conn.execute('SELECT COUNT(*) FROM exam_sessions WHERE thread_id=?', (tid,)).fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM exam_tool_events WHERE thread_id=?', (tid,)).fetchone()[0] == 0


def test_exam_agent_releases_database_lock_during_model_call(client, monkeypatch):
    tid = make_thread(client)
    client.post(f'/api/threads/{tid}/exam/start')

    async def model_tool_call(message, session, settings, **kwargs):
        # An independent connection must not be blocked by tool telemetry while
        # the provider is generating its next function call.
        with service.database() as conn:
            conn.execute("UPDATE threads SET title='still responsive' WHERE id=?", (tid,))
        return [('save_exam_requirements', {'faculty_initials': 'AB'})]

    monkeypatch.setattr(exam_agent, 'propose_tool_calls', model_tool_call)
    response = client.post(f'/api/threads/{tid}/exam/turn', json={'content': 'Faculty initials AB'})
    assert response.status_code == 200, response.text
    assert response.json()['session']['requirements']['faculty_initials'] == 'AB'
    with service.database() as conn:
        assert conn.execute('SELECT title FROM threads WHERE id=?', (tid,)).fetchone()[0] == 'still responsive'


def test_exam_carries_chat_attachments_as_unapproved_selections(client):
    cid = client.get('/api/courses').json()[0]['id']
    tid = make_thread(client, cid)
    file = client.post(f'/api/courses/{cid}/documents', files={
        'file': ('algorithms.txt', b'Breadth first search uses a queue in graph traversal')})
    assert file.status_code == 201, file.text
    doc = file.json()['id']
    import time
    for _ in range(100):
        status = client.get(f'/api/courses/{cid}/documents/{doc}').json()['status']
        if status == 'ready':
            break
        time.sleep(.02)
    assert status == 'ready'
    with service.database() as conn:
        mid = service.identifier()
        conn.execute("INSERT INTO messages(id,thread_id,role,content,created_at) VALUES (?,?, 'user',?,?)",
                     (mid, tid, 'Use the uploaded graph lecture for my midterm', service.now()))
        conn.execute('INSERT INTO message_documents(message_id,document_id,position) VALUES(?,?,0)',
                     (mid, doc))
    base = f'/api/threads/{tid}/exam'
    session = client.post(base + '/start').json()
    assert session['document_ids'] == [doc]
    assert session['status'] == 'collecting'
    saved = complete(client, tid, scope='selected')
    assert saved.json()['ready_for_approval']
    assert client.post(base + '/approve').json()['status'] == 'approved'
    cleared = client.patch(base + '/requirements', json={'updates': {'material_scope': 'general'}}).json()
    assert cleared['status'] == 'awaiting_approval'
    assert cleared['document_ids'] == []


def test_quick_replies_advance_one_question_and_persist_as_messages(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    assert client.post(base + '/start').status_code == 200
    choices = [
        ({'faculty_initials': 'FI'}, 'FI', 'exam_type'),
        ({'university_name': 'Example University'}, 'Example University', 'exam_type'),
        ({'exam_type': 'midterm'}, 'Midterm', 'total_marks'),
        ({'total_marks': 40}, '40 marks', 'question_formats'),
        ({'difficulty': 'moderate'}, 'Moderate', 'question_formats'),
        ({'question_formats': [{'kind': 'mcq', 'count': 10, 'marks_each': 1},
                               {'kind': 'short_answer', 'count': 6, 'marks_each': 5}]},
         '10 MCQs and 6 short answers', 'material_scope'),
        ({'material_scope': 'general'}, 'General knowledge', None),
    ]
    for updates, answer, next_field in choices:
        response = client.post(base + '/quick-reply', json={'updates': updates, 'answer': answer})
        assert response.status_code == 200, response.text
        session = response.json()['session']
        assert (session['missing'][0] if session['missing'] else None) == next_field
        assert response.json()['user_id'] and response.json()['message_id']
    assert session['status'] == 'awaiting_approval'
    messages = client.get(f'/api/threads/{tid}/messages').json()
    assert [m['role'] for m in messages] == ['user', 'assistant'] * len(choices)
    assert [m['content'] for m in messages if m['role'] == 'user'] == [answer for _,answer,_ in choices]
    assert all(m['mode'] == 'exam' for m in messages)
    service.init_db()
    assert client.get(base).json()['requirements']['total_marks'] == 40
    assert client.post(base + '/approve').status_code == 200


def test_quick_reply_validation_does_not_write_untrusted_turn(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    assert client.post(base + '/quick-reply', json={
        'updates': {'difficulty': 'super-impossible'}, 'answer': 'Anything'}).status_code == 422
    assert client.post(base + '/quick-reply', json={
        'updates': {}, 'answer': 'Nothing'}).status_code == 422
    assert client.get(f'/api/threads/{tid}/messages').json() == []
    assert client.get(base).json()['requirements'] == {}


def test_question_format_breakdown_can_be_answered_in_regular_exam_chat(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    client.patch(base + '/requirements', json={'updates': {
        'faculty_initials': 'FI', 'university_name': 'Example University',
        'exam_type': 'midterm', 'total_marks': 40, 'difficulty': 'moderate'}})
    result = client.post(base + '/turn', json={
        'content': '10 MCQs x 1 mark and 6 short answers x 5 marks'})
    assert result.status_code == 200, result.text
    assert result.json()['session']['requirements']['question_formats'] == [
        {'kind': 'mcq', 'count': 10, 'marks_each': 1},
        {'kind': 'short_answer', 'count': 6, 'marks_each': 5},
    ]
    assert result.json()['session']['missing'] == ['material_scope']


def test_custom_five_mark_mix_is_saved_and_advances_exam(client):
    """Regression: a valid three-type 5/5 custom mix must save via Use this mix."""
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    assert client.post(base + '/start').status_code == 200
    initial = client.patch(base + '/requirements', json={'updates': {
        'faculty_initials': 'FI',
        'university_name': 'Example University',
        'exam_type': 'quiz',
        'total_marks': 5,
        'difficulty': 'moderate',
    }})
    assert initial.status_code == 200
    assert initial.json()['missing'][0] == 'question_formats'
    mix = [
        {'kind': 'mcq', 'count': 1, 'marks_each': 1},
        {'kind': 'problem_solving', 'count': 1, 'marks_each': 2},
        {'kind': 'short_answer', 'count': 1, 'marks_each': 2},
    ]
    response = client.post(base + '/quick-reply', json={
        'updates': {'question_formats': mix},
        'answer': '1 Mcq × 1 · 1 Problem Solving × 2 · 1 Short Answer × 2',
    })
    assert response.status_code == 200, response.text
    assert response.json()['session']['requirements']['question_formats'] == mix
    assert response.json()['session']['missing'] == ['material_scope']
    assert client.get(base).json()['requirements']['question_formats'] == mix
    history = client.get(f'/api/threads/{tid}/messages').json()
    assert history[-2]['role'] == 'user'
    assert 'Problem Solving' in history[-2]['content']
    assert client.post(base + '/quick-reply', json={
        'updates': {'material_scope': 'general'}, 'answer': 'General knowledge'
    }).json()['session']['ready_for_approval'] is True


def test_invalid_custom_mix_is_rejected_without_creating_a_chat_turn(client):
    tid = make_thread(client)
    base = f'/api/threads/{tid}/exam'
    client.post(base + '/start')
    bad = client.post(base + '/quick-reply', json={
        'updates': {'question_formats': [
            {'kind': 'mcq', 'count': 0, 'marks_each': 1},
            {'kind': 'short_answer', 'count': 1, 'marks_each': 5},
        ]},
        'answer': '5 of 5 marks',
    })
    assert bad.status_code == 422
    assert client.get(f'/api/threads/{tid}/messages').json() == []
