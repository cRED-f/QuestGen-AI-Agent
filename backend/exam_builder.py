"""Professor-controlled exam-paper builder with batch draft support.

Each thread owns a durable blueprint, candidate, accepted questions and versions.
Only explicit human actions modify the paper. Model calls never hold SQLite locks.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

import httpx
import json
import re
import sqlite3
import time
import uuid
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from backend import documents, exam_state, generation
from backend.marks import to_cents

MAX_SLOTS = 100
GENERATION_TIMEOUT_SECONDS = 90
logger = logging.getLogger(__name__)


def stamp() -> float:
    return time.time()


def uid() -> str:
    return str(uuid.uuid4())


class Section(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    kind: str
    count: int = Field(ge=1, le=100)
    marks_each: float = Field(ge=0.01, le=100)
    topic: str = Field(min_length=2, max_length=200)
    difficulty: str

    @model_validator(mode='after')
    def valid(self):
        to_cents(self.marks_each)
        if self.kind not in exam_state.FORMATS or self.difficulty not in (*exam_state.DIFFICULTIES, 'auto'):
            raise ValueError('Unsupported section type or difficulty')
        return self


class Blueprint(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    instructions: str = Field(default='Answer all questions.', max_length=800)
    sections: list[Section] = Field(min_length=1, max_length=12)


class CandidateEdit(BaseModel):
    stem: str = Field(min_length=5, max_length=2500)
    options: list[str] = Field(default_factory=list, max_length=4)
    correct_answer: str = Field(min_length=1, max_length=3000)
    explanation: str = Field(min_length=1, max_length=2500)


class MoveInput(BaseModel):
    # A complete permutation ensures no accepted question is inadvertently deleted.
    question_ids: list[str] = Field(max_length=MAX_SLOTS)


def signature(session: dict) -> str:
    data = {'requirements': session['requirements'], 'document_ids': sorted(session['document_ids']),
            'approved_at': session['approved_at']}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS exam_papers (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        status TEXT NOT NULL,
        blueprint TEXT NOT NULL,
        session_signature TEXT NOT NULL,
        material_ids TEXT NOT NULL DEFAULT '[]',
        next_source TEXT,
        candidate TEXT,
        candidate_id TEXT,
        candidate_slot INTEGER,
        generation_token TEXT,
        generation_started REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        final_approved_at REAL,
        final_signature TEXT
    );
    CREATE TABLE IF NOT EXISTS exam_paper_questions (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES exam_papers(thread_id) ON DELETE CASCADE,
        slot INTEGER NOT NULL,
        position INTEGER NOT NULL,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        accepted_candidate_id TEXT,
        UNIQUE(thread_id,slot), UNIQUE(thread_id,position)
    );
    CREATE TABLE IF NOT EXISTS exam_paper_versions (
        question_id TEXT NOT NULL REFERENCES exam_paper_questions(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        content TEXT NOT NULL,
        action TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(question_id,version)
    );
    CREATE TABLE IF NOT EXISTS exam_candidate_history (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES exam_papers(thread_id) ON DELETE CASCADE,
        slot INTEGER NOT NULL,
        stem TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS exam_paper_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL REFERENCES exam_papers(thread_id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        duration_ms INTEGER,
        message TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS exam_paper_events_thread ON exam_paper_events(thread_id, id);
    CREATE TABLE IF NOT EXISTS exam_paper_generation_requests (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES exam_papers(thread_id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS exam_generation_thread ON exam_paper_generation_requests(thread_id,created_at DESC);
    ''')
    for name, definition in (('final_approved_at', 'REAL'), ('final_signature', 'TEXT')):
        if name not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_papers)')}:
            conn.execute(f'ALTER TABLE exam_papers ADD COLUMN {name} {definition}')
    if 'accepted_candidate_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_paper_questions)')}:
        conn.execute('ALTER TABLE exam_paper_questions ADD COLUMN accepted_candidate_id TEXT')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS exam_accepted_candidates ON exam_paper_questions(thread_id, accepted_candidate_id) WHERE accepted_candidate_id IS NOT NULL')
    if 'material_ids' not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_papers)')}:
        conn.execute("ALTER TABLE exam_papers ADD COLUMN material_ids TEXT NOT NULL DEFAULT '[]'")
    if 'next_source' not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_papers)')}:
        conn.execute('ALTER TABLE exam_papers ADD COLUMN next_source TEXT')


def _slots(blueprint: Blueprint) -> list[dict]:
    slots = []
    for section in blueprint.sections:
        for index in range(section.count):
            slots.append({'ordinal': len(slots) + 1, 'section_id': section.id,
                          'section_number': index + 1, 'kind': section.kind,
                          'marks': section.marks_each, 'topic': section.topic,
                          'difficulty': section.difficulty})
    if len(slots) > MAX_SLOTS:
        raise HTTPException(status_code=422, detail=f'Paper supports at most {MAX_SLOTS} questions')
    return slots


def _validate_blueprint(blueprint: Blueprint, requirements: dict) -> list[dict]:
    slots = _slots(blueprint)
    if len({section.id for section in blueprint.sections}) != len(blueprint.sections):
        raise HTTPException(status_code=422, detail='Blueprint section IDs must be unique')
    actual = [(s.kind, s.count, s.marks_each) for s in blueprint.sections]
    required = [(s['kind'], s['count'], s['marks_each']) for s in requirements['question_formats']]
    # Allow professors to modify topics/title/difficulty, but not bypass approved distribution.
    if actual != required:
        raise HTTPException(status_code=409, detail='Section types, counts and marks must match approved configuration')
    if sum(to_cents(s['marks']) for s in slots) != to_cents(requirements['total_marks'], max_marks=1000):
        raise HTTPException(status_code=409, detail='Paper marks differ from approved total')
    return slots


def _valid_paper(conn: sqlite3.Connection, thread_id: str, *, require_ready: bool = False) -> tuple[dict, dict]:
    session = exam_state.require_approval(conn, thread_id)
    row = conn.execute('SELECT * FROM exam_papers WHERE thread_id=?', (thread_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=409, detail='Prepare and approve a blueprint first')
    paper = dict(row)
    if paper['session_signature'] != signature(session):
        raise HTTPException(status_code=409, detail='Exam configuration has changed. Prepare a new blueprint')
    if session['requirements'].get('material_scope') == 'all_course':
        ready_ids = {r['id'] for r in conn.execute(
            "SELECT id FROM documents WHERE course_id=? AND status='ready'", (session['course_id'],))}
        if ready_ids != set(json.loads(paper['material_ids'])):
            raise HTTPException(status_code=409, detail='Course materials changed; prepare a new blueprint')
    if require_ready and paper['status'] not in ('ready', 'complete', 'generating'):
        raise HTTPException(status_code=409, detail='Approve the blueprint before generating questions')
    return paper, session


def _revision_fingerprint(conn: sqlite3.Connection, paper: dict) -> str:
    """Approve a precise version, including section order and all accepted contents."""
    questions = [dict(row) for row in conn.execute(
        'SELECT id,slot,position,version,content FROM exam_paper_questions WHERE thread_id=? ORDER BY position',
        (paper['thread_id'],))]
    payload = {'session': paper['session_signature'], 'materials': json.loads(paper['material_ids']),
               'blueprint': json.loads(paper['blueprint']), 'questions': questions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()


def _event(conn: sqlite3.Connection, thread_id: str, event: str, duration_ms: int | None = None,
           message: str = '') -> None:
    conn.execute('INSERT INTO exam_paper_events(thread_id,event,duration_ms,message,created_at) VALUES (?,?,?,?,?)',
                 (thread_id, event, duration_ms, message[:200], stamp()))


def approve_final(conn: sqlite3.Connection, thread_id: str) -> dict:
    paper, session = _valid_paper(conn, thread_id, require_ready=True)
    if paper['status'] != 'complete' or paper['candidate'] or paper['generation_token'] or conn.execute('SELECT 1 FROM exam_paper_drafts WHERE thread_id=? LIMIT 1',(thread_id,)).fetchone():
        raise HTTPException(status_code=409, detail='Finish every question and resolve the candidate before final approval')
    slots = _validate_blueprint(Blueprint.model_validate_json(paper['blueprint']), session['requirements'])
    counts = [row['slot'] for row in conn.execute('SELECT slot FROM exam_paper_questions WHERE thread_id=?', (thread_id,))]
    if len(counts) != len(slots) or set(counts) != {slot['ordinal'] for slot in slots}:
        raise HTTPException(status_code=409, detail='Every paper slot must contain an accepted question')
    approval = _revision_fingerprint(conn, paper)
    conn.execute('UPDATE exam_papers SET final_approved_at=?,final_signature=?,updated_at=? WHERE thread_id=?',
                 (stamp(), approval, stamp(), thread_id))
    _event(conn, thread_id, 'final_approved')
    return read(conn, thread_id)


def approved_for_export(conn: sqlite3.Connection, thread_id: str) -> tuple[dict, dict]:
    paper, session = _valid_paper(conn, thread_id, require_ready=True)
    if paper['status'] != 'complete' or not paper['final_signature'] or not paper['final_approved_at']:
        raise HTTPException(status_code=409, detail='Review and approve the completed paper before downloading')
    if _revision_fingerprint(conn, paper) != paper['final_signature']:
        raise HTTPException(status_code=409, detail='The paper has changed. Approve the latest version before downloading')
    return paper, session


def read(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute('SELECT * FROM exam_papers WHERE thread_id=?', (thread_id,)).fetchone()
    if not row:
        return None
    paper = dict(row)
    paper['blueprint'] = json.loads(paper['blueprint'])
    paper['material_ids'] = json.loads(paper['material_ids'])
    paper['next_source'] = json.loads(paper['next_source']) if paper['next_source'] else None
    paper['candidate'] = json.loads(paper['candidate']) if paper['candidate'] else None
    slots = _slots(Blueprint.model_validate(paper['blueprint']))
    questions = [dict(r) for r in conn.execute('SELECT * FROM exam_paper_questions WHERE thread_id=? ORDER BY position', (thread_id,))]
    for q in questions:
        q['content'] = json.loads(q['content'])
    accepted = {q['slot'] for q in questions}
    paper['questions'] = questions
    from backend import exam_batch
    paper['drafts'] = exam_batch.drafts(conn, thread_id)
    paper['final_approved'] = (bool(paper['final_signature'] and paper['final_approved_at'])
        and paper['status'] == 'complete' and _revision_fingerprint(conn, dict(row)) == paper['final_signature'])
    paper['final_approved_at'] = paper['final_approved_at'] if paper['final_approved'] else None
    paper.pop('final_signature', None)
    total_cents = sum(to_cents(s['marks']) for s in slots)
    accepted_cents = sum(to_cents(q['content']['marks']) for q in questions)
    paper['total_marks'] = total_cents / 100
    paper['accepted_marks'] = accepted_cents / 100
    paper['remaining_marks'] = (total_cents - accepted_cents) / 100
    paper['total_questions'] = len(slots)
    paper['next_slot'] = next((s for s in slots if s['ordinal'] not in accepted), None)
    if paper['next_slot'] and paper['next_source'] and paper['next_slot']['ordinal'] == paper['next_source']['slot']:
        paper['next_slot']['topic'] = paper['next_source']['topic']
    session = exam_state.read(conn, thread_id)
    paper['stale'] = not session or session['status'] != 'approved' or paper['session_signature'] != signature(session)
    if session and session['requirements'].get('material_scope') == 'all_course':
        ready_ids = {r['id'] for r in conn.execute(
            "SELECT id FROM documents WHERE course_id=? AND status='ready'", (session['course_id'],))}
        paper['stale'] = paper['stale'] or ready_ids != set(paper['material_ids'])
    paper['final_approved'] = paper['final_approved'] and not paper['stale']
    if paper['stale']:
        paper['final_approved_at'] = None
    # Tokens are for server execution only and should never be exposed to the browser.
    paper.pop('generation_token', None)
    return paper


def prepare(conn: sqlite3.Connection, thread_id: str) -> dict:
    session = exam_state.require_approval(conn, thread_id)
    current = read(conn, thread_id)
    if current and not current['stale']:
        return current
    req = session['requirements']
    selected = session['document_ids'] if req['material_scope'] == 'selected' else []
    if req['material_scope'] != 'general':
        if req['material_scope'] == 'all_course':
            selected = [r['id'] for r in conn.execute("SELECT id FROM documents WHERE course_id=? AND status='ready'", (session['course_id'],))]
        if not selected:
            raise HTTPException(status_code=409, detail='Upload a ready course material or select general knowledge')
        try:
            documents.verify_course_documents(conn, session['course_id'], selected)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        samples = documents.sample_course_sources(session['course_id'], 12, selected)
        labels = []
        for src in samples:
            label = (src.get('section') or '').strip()
            if not label or len(label) < 3:
                label = src.get('filename', 'Course concepts').rsplit('.', 1)[0]
            if label not in labels:
                labels.append(label[:200])
    else:
        labels = []
    default_topic = req.get('topic_scope') or 'Course concepts selected by professor'
    sections = [Section(id=f'section-{i+1}', kind=f['kind'], count=f['count'], marks_each=f['marks_each'],
                        topic=labels[i % len(labels)] if labels else default_topic, difficulty=req.get('difficulty', 'auto'))
                for i, f in enumerate(req['question_formats'])]
    blueprint = Blueprint(title=f"{req.get('university_name', 'Course')} — {req.get('exam_type', 'custom').title()} Examination",
                          sections=sections)
    _validate_blueprint(blueprint, req)
    now = stamp()
    conn.execute('INSERT INTO exam_papers(thread_id,status,blueprint,session_signature,material_ids,created_at,updated_at) '
                 "VALUES(?,'awaiting_approval',?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                 "status='awaiting_approval',blueprint=excluded.blueprint,session_signature=excluded.session_signature,material_ids=excluded.material_ids,"
                 'candidate=NULL,candidate_id=NULL,candidate_slot=NULL,generation_token=NULL,next_source=NULL,final_approved_at=NULL,final_signature=NULL,updated_at=excluded.updated_at',
                 (thread_id, blueprint.model_dump_json(), signature(session), json.dumps(selected), now, now))
    # A changed configuration invalidates the whole draft; never silently reuse accepted questions.
    conn.execute('DELETE FROM exam_paper_questions WHERE thread_id=?', (thread_id,))
    conn.execute('DELETE FROM exam_paper_drafts WHERE thread_id=?', (thread_id,))
    conn.execute('DELETE FROM exam_paper_contexts WHERE thread_id=?', (thread_id,))
    conn.execute('DELETE FROM exam_candidate_history WHERE thread_id=?', (thread_id,))
    return read(conn, thread_id)


def change_blueprint(conn: sqlite3.Connection, thread_id: str, value: Blueprint) -> dict:
    paper, session = _valid_paper(conn, thread_id)
    if paper['status'] != 'awaiting_approval':
        raise HTTPException(status_code=409, detail='Cannot edit an approved blueprint; change requirements and create a new draft')
    _validate_blueprint(value, session['requirements'])
    conn.execute('DELETE FROM exam_paper_contexts WHERE thread_id=?', (thread_id,))
    conn.execute('UPDATE exam_papers SET blueprint=?,updated_at=? WHERE thread_id=?',
                 (value.model_dump_json(), stamp(), thread_id))
    return read(conn, thread_id)


def approve(conn: sqlite3.Connection, thread_id: str) -> dict:
    paper, session = _valid_paper(conn, thread_id)
    if paper['status'] != 'awaiting_approval':
        raise HTTPException(status_code=409, detail='Blueprint is already approved')
    _validate_blueprint(Blueprint.model_validate_json(paper['blueprint']), session['requirements'])
    conn.execute("UPDATE exam_papers SET status='ready',updated_at=? WHERE thread_id=?", (stamp(), thread_id))
    return read(conn, thread_id)


def _current_slot(conn: sqlite3.Connection, thread_id: str, paper: dict) -> dict:
    slots = _slots(Blueprint.model_validate_json(paper['blueprint']))
    accepted = {r['slot'] for r in conn.execute('SELECT slot FROM exam_paper_questions WHERE thread_id=?', (thread_id,))}
    slot = next((s for s in slots if s['ordinal'] not in accepted), None)
    if not slot:
        raise HTTPException(status_code=409, detail='All question slots are filled')
    return slot


def request_next_chapter(conn: sqlite3.Connection, thread_id: str, message: str) -> dict:
    """Honor a professor's explicit next-question chapter request.

    This is a paper-level command, not a change to the approved global material
    scope or the accepted questions. Reject unapproved/missing files, never
    silently fall back to a different chapter.
    """
    paper, session = _valid_paper(conn, thread_id, require_ready=True)
    if paper['status'] not in ('ready',):
        raise HTTPException(status_code=409, detail='Finish blueprint approval or current generation before changing the next question')
    if session['requirements']['material_scope'] == 'general':
        raise HTTPException(status_code=409, detail='This exam uses general knowledge. Select course materials and approve the configuration to generate from a chapter PPTX.')
    slot = _current_slot(conn, thread_id, paper)
    allowed = set(json.loads(paper['material_ids']))
    materials = [dict(row) for row in conn.execute(
        "SELECT id,filename,extension,status FROM documents WHERE course_id=? AND status='ready'",
        (session['course_id'],))]
    # Prefer PPTX when professor asks for a chapter PPTX, but avoid mixing
    # chapter 3 with chapter 03 of a *different* chapter.
    matches = documents.requested_presentation_ids(message, materials)
    if not matches:
        matches = documents.requested_document_ids(message, materials)
    if not matches:
        raise HTTPException(status_code=422, detail='I could not find a ready file matching that chapter in this course. Upload or select the chapter PPTX first.')
    if not set(matches).issubset(allowed):
        raise HTTPException(status_code=409, detail='That chapter is outside the approved material selection. Select the file and reapprove the exam configuration before generating from it.')
    filenames = [item['filename'] for item in materials if item['id'] in matches]
    # Prefer content-neutral filename over the stale blueprint section topic.
    topic = ', '.join(filenames)[:200]
    source = {'slot': slot['ordinal'], 'document_ids': matches, 'topic': topic}
    if paper['candidate']:
        old = json.loads(paper['candidate'])
        conn.execute('INSERT INTO exam_candidate_history(id,thread_id,slot,stem,reason,created_at) VALUES(?,?,?,?,?,?)',
                     (uid(), thread_id, slot['ordinal'], old['stem'], 'chapter_changed', stamp()))
    conn.execute("""UPDATE exam_papers SET next_source=?,candidate=NULL,candidate_id=NULL,
        candidate_slot=NULL,final_approved_at=NULL,final_signature=NULL,updated_at=? WHERE thread_id=?""",
        (json.dumps(source), stamp(), thread_id))
    _event(conn, thread_id, 'next_chapter_changed', message=topic)
    return source


def _validate_content(data: dict, slot: dict) -> dict:
    try:
        edited = CandidateEdit.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail='Question content is invalid') from exc
    if slot['kind'] == 'mcq':
        if len(edited.options) != 4 or len({x.strip().casefold() for x in edited.options}) != 4:
            raise HTTPException(status_code=422, detail='MCQ requires four different options')
        if edited.correct_answer not in edited.options:
            raise HTTPException(status_code=422, detail='Correct answer must be one option')
    elif edited.options:
        raise HTTPException(status_code=422, detail='Non-MCQ must not have answer options')
    return edited.model_dump()


def _not_duplicate(conn: sqlite3.Connection, thread_id: str, stem: str, *, exclude: str | None = None) -> None:
    norm = re.sub(r'\s+', ' ', stem).strip().casefold()
    for row in conn.execute('SELECT id,content FROM exam_paper_questions WHERE thread_id=?', (thread_id,)):
        if row['id'] != exclude and re.sub(r'\s+', ' ', json.loads(row['content'])['stem']).strip().casefold() == norm:
            raise HTTPException(status_code=409, detail='This question already exists in the paper')


def _save_version(conn: sqlite3.Connection, question_id: str, version: int, content: dict, action: str):
    conn.execute('INSERT INTO exam_paper_versions(question_id,version,content,action,created_at) VALUES(?,?,?,?,?)',
                 (question_id, version, json.dumps(content), action, stamp()))


def _candidate(conn: sqlite3.Connection, thread_id: str, candidate_id: str) -> tuple[dict, dict, dict]:
    paper, _ = _valid_paper(conn, thread_id, require_ready=True)
    if paper['status'] != 'ready' or not paper['candidate'] or paper['candidate_id'] != candidate_id:
        raise HTTPException(status_code=409, detail='Candidate changed or no longer available')
    slot = _current_slot(conn, thread_id, paper)
    if paper['candidate_slot'] != slot['ordinal']:
        raise HTTPException(status_code=409, detail='Candidate slot is out of date')
    return paper, slot, json.loads(paper['candidate'])


def accept(conn: sqlite3.Connection, thread_id: str, candidate_id: str) -> dict:
    existing = conn.execute('SELECT 1 FROM exam_paper_questions WHERE thread_id=? AND accepted_candidate_id=?',
                            (thread_id, candidate_id)).fetchone()
    if existing:
        _valid_paper(conn, thread_id, require_ready=True)
        return read(conn, thread_id)
    paper, slot, data = _candidate(conn, thread_id, candidate_id)
    _not_duplicate(conn, thread_id, data['stem'])
    content = {**data, **{k: slot[k] for k in ('ordinal','section_id','kind','marks','difficulty')}}
    content['topic'] = data.get('topic', slot['topic'])
    qid, now = uid(), stamp()
    pos = conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM exam_paper_questions WHERE thread_id=?', (thread_id,)).fetchone()[0]
    conn.execute('INSERT INTO exam_paper_questions(id,thread_id,slot,position,content,version,created_at,updated_at,accepted_candidate_id) VALUES(?,?,?,?,?,1,?,?,?)',
                 (qid, thread_id, slot['ordinal'], pos, json.dumps(content), now, now, candidate_id))
    _save_version(conn, qid, 1, content, 'accepted')
    _event(conn, thread_id, 'candidate_accepted')
    next_slot = _current_slot_if_exists(conn, thread_id, paper)
    conn.execute('UPDATE exam_papers SET candidate=NULL,candidate_id=NULL,candidate_slot=NULL,next_source=NULL,status=?,updated_at=? WHERE thread_id=?',
                 ('ready' if next_slot else 'complete', now, thread_id))
    return read(conn, thread_id)


def _current_slot_if_exists(conn: sqlite3.Connection, thread_id: str, paper: dict) -> dict | None:
    try:
        return _current_slot(conn, thread_id, paper)
    except HTTPException as exc:
        if exc.status_code == 409 and exc.detail == 'All question slots are filled':
            return None
        raise


def discard(conn: sqlite3.Connection, thread_id: str, candidate_id: str, reason: str = 'skipped') -> dict:
    paper, slot, candidate = _candidate(conn, thread_id, candidate_id)
    conn.execute('INSERT INTO exam_candidate_history(id,thread_id,slot,stem,reason,created_at) VALUES(?,?,?,?,?,?)',
                 (uid(), thread_id, slot['ordinal'], candidate['stem'], reason, stamp()))
    conn.execute('UPDATE exam_papers SET candidate=NULL,candidate_id=NULL,candidate_slot=NULL,updated_at=? WHERE thread_id=?',
                 (stamp(), thread_id))
    return read(conn, thread_id)


def edit_candidate(conn: sqlite3.Connection, thread_id: str, candidate_id: str, changes: CandidateEdit) -> dict:
    paper, slot, data = _candidate(conn, thread_id, candidate_id)
    value = _validate_content(changes.model_dump(), slot)
    _not_duplicate(conn, thread_id, value['stem'])
    # Preserve verified citation metadata only if text has not been materially edited.
    data.update(value)
    if any(value[key] != json.loads(paper['candidate'])[key] for key in value):
        data['source_ids'] = []
        data['sources'] = []
        data['grounded'] = False
        data['evaluation'] = {'passed': False, 'issues': ['Professor edited; review before export']}
    conn.execute('UPDATE exam_papers SET candidate=?,updated_at=? WHERE thread_id=?', (json.dumps(data), stamp(), thread_id))
    return read(conn, thread_id)


def _question(conn: sqlite3.Connection, thread_id: str, question_id: str) -> dict:
    row = conn.execute('SELECT * FROM exam_paper_questions WHERE id=? AND thread_id=?', (question_id, thread_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Question not in this paper')
    return dict(row)


def edit_question(conn: sqlite3.Connection, thread_id: str, question_id: str, changes: CandidateEdit) -> dict:
    _valid_paper(conn, thread_id, require_ready=True)
    row = _question(conn, thread_id, question_id)
    data = json.loads(row['content'])
    value = _validate_content(changes.model_dump(), data)
    _not_duplicate(conn, thread_id, value['stem'], exclude=question_id)
    if any(value[key] != data[key] for key in value):
        data.update(source_ids=[],sources=[],grounded=False,evaluation={'passed': False,'issues':['Professor edited; review before export']})
    data.update(value)
    version = row['version'] + 1
    conn.execute('UPDATE exam_paper_questions SET content=?,version=?,updated_at=? WHERE id=?',
                 (json.dumps(data), version, stamp(), question_id))
    _save_version(conn, question_id, version, data, 'edited')
    conn.execute('UPDATE exam_papers SET final_approved_at=NULL,final_signature=NULL WHERE thread_id=?', (thread_id,))
    _event(conn, thread_id, 'question_edited')
    return read(conn, thread_id)


def remove(conn: sqlite3.Connection, thread_id: str, question_id: str) -> dict:
    paper, _ = _valid_paper(conn, thread_id, require_ready=True)
    _question(conn, thread_id, question_id)
    conn.execute('DELETE FROM exam_paper_questions WHERE id=? AND thread_id=?', (question_id, thread_id))
    # Avoid UNIQUE(thread_id, position) collisions when compacting the positions.
    rows = conn.execute('SELECT id FROM exam_paper_questions WHERE thread_id=? ORDER BY position', (thread_id,)).fetchall()
    for i, r in enumerate(rows):
        conn.execute('UPDATE exam_paper_questions SET position=? WHERE id=?', (-(i+1), r['id']))
    for i, r in enumerate(rows):
        conn.execute('UPDATE exam_paper_questions SET position=? WHERE id=?', (i+1, r['id']))
    conn.execute("UPDATE exam_papers SET status='ready',candidate=NULL,candidate_id=NULL,candidate_slot=NULL,next_source=NULL,final_approved_at=NULL,final_signature=NULL,updated_at=? WHERE thread_id=?",
                 (stamp(), thread_id))
    _event(conn, thread_id, 'question_removed')
    return read(conn, thread_id)


def reorder(conn: sqlite3.Connection, thread_id: str, ids: list[str]) -> dict:
    _valid_paper(conn, thread_id, require_ready=True)
    rows = conn.execute('SELECT id FROM exam_paper_questions WHERE thread_id=?', (thread_id,)).fetchall()
    if len(ids) != len(set(ids)) or set(ids) != {r['id'] for r in rows}:
        raise HTTPException(status_code=422, detail='Ordering must include each accepted question exactly once')
    paper = read(conn, thread_id)
    section_order = {section['id']: index for index, section in enumerate(paper['blueprint']['sections'])}
    by_id = {question['id']: question['content']['section_id'] for question in paper['questions']}
    sequence = [section_order[by_id[item]] for item in ids]
    if sequence != sorted(sequence):
        raise HTTPException(status_code=422, detail='Questions must remain within their exam sections')
    for i, item in enumerate(ids):
        conn.execute('UPDATE exam_paper_questions SET position=? WHERE id=?', (-(i+1), item))
    for i, item in enumerate(ids):
        conn.execute('UPDATE exam_paper_questions SET position=? WHERE id=?', (i+1, item))
    conn.execute('UPDATE exam_papers SET final_approved_at=NULL,final_signature=NULL WHERE thread_id=?', (thread_id,))
    _event(conn, thread_id, 'questions_reordered')
    return read(conn, thread_id)


def versions(conn: sqlite3.Connection, thread_id: str, question_id: str) -> list[dict]:
    _question(conn, thread_id, question_id)
    return [dict(id=q['version'], version=q['version'], action=q['action'], created_at=q['created_at'])
            for q in conn.execute('SELECT version,action,created_at FROM exam_paper_versions WHERE question_id=? ORDER BY version DESC', (question_id,))]


async def generate_next(thread_id: str, *, regenerate: bool = False, on_reasoning=None) -> dict:
    from backend.app import database, model_credentials
    # Atomic reservation ensures a double-click never generates two candidates.
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        paper, session = _valid_paper(conn, thread_id, require_ready=True)
        if paper['status'] == 'generating':
            if paper['generation_started'] and stamp() - paper['generation_started'] < 180:
                raise HTTPException(status_code=409, detail='A question is already generating')
            # Recovery for an interrupted process.
        elif paper['status'] != 'ready':
            raise HTTPException(status_code=409, detail='Finish reviewing the paper before generating more')
        if paper['candidate'] and not regenerate:
            raise HTTPException(status_code=409, detail='Accept, edit, skip or regenerate the current candidate first')
        slot = _current_slot(conn, thread_id, paper)
        source_override = json.loads(paper['next_source']) if paper['next_source'] else None
        if source_override:
            if source_override['slot'] != slot['ordinal']:
                raise HTTPException(status_code=409, detail='Chapter selection no longer matches the next question slot')
            allowed = set(json.loads(paper['material_ids']))
            if not set(source_override['document_ids']).issubset(allowed):
                raise HTTPException(status_code=409, detail='Requested chapter is outside the approved materials')
            slot = {**slot, 'topic': source_override['topic']}
        history = [r['stem'] for r in conn.execute('SELECT stem FROM exam_candidate_history WHERE thread_id=? AND slot=? ORDER BY created_at DESC LIMIT 12', (thread_id, slot['ordinal']))]
        accepted_stems = [json.loads(r['content'])['stem'] for r in conn.execute('SELECT content FROM exam_paper_questions WHERE thread_id=?', (thread_id,))]
        if paper['candidate']:
            history.append(json.loads(paper['candidate'])['stem'])
        row = conn.execute('SELECT provider,model FROM workspace_settings WHERE id=1').fetchone()
        settings = dict(row)
        if not settings['model']:
            raise HTTPException(status_code=409, detail='Choose a model to generate questions')
        model_credentials(settings)
        token = uid()
        conn.execute("UPDATE exam_papers SET status='generating',generation_token=?,generation_started=?,updated_at=? WHERE thread_id=?",
                     (token, stamp(), stamp(), thread_id))
    try:
        selected = json.loads(paper['material_ids'])
        if source_override:
            selected = source_override['document_ids']
            # A filename is not necessarily searchable in slide text. Sample
            # the requested deck if lexical retrieval finds no passages.
            sources = documents.retrieve(session['course_id'], slot['topic'], 6, selected)
            if not sources:
                sources = documents.sample_course_sources(session['course_id'], 6, selected)
            if not sources:
                raise HTTPException(status_code=422, detail='The requested chapter has no indexed text. Reprocess its PPTX before generating.')
        elif session['requirements']['material_scope'] == 'general':
            sources = []
        else:
            sources = documents.retrieve(session['course_id'], slot['topic'], 6, selected)
        kinds = {'long_answer': 'long_answer', 'custom': 'custom'}
        from backend.exam_context import complexity
        level, cognitive = complexity(slot, session['requirements'].get('exam_type', 'custom'))
        plan = generation.QuestionPlan(topic=slot['topic'], kind=kinds.get(slot['kind'], slot['kind']),
                                       difficulty={'hard++':'hard','complex':'hard','moderate':'medium','easy':'easy'}[level],
                                       bloom_level=cognitive,
                                       marks=slot['marks'])
        feedback = ('Use ONLY the supplied excerpts from ' + slot['topic'] +
                    '. Cite at least one supplied chunk ID and do not reuse the previous chapter.\n' if source_override else '') + 'Do not repeat any of these previously rejected or proposed questions: ' + json.dumps((accepted_stems + history)[-30:])
        last_error = 'Question generation failed'
        started = time.monotonic()
        for _ in range(generation.MAX_ATTEMPTS):
            try:
                question = await asyncio.wait_for(
                    generation.generate_question(settings, plan, sources, feedback,
                                                 on_reasoning=on_reasoning),
                    timeout=GENERATION_TIMEOUT_SECONDS)
                normalized = re.sub(r'\s+', ' ', question.stem).strip().casefold()
                if any(re.sub(r'\s+', ' ', h).strip().casefold() == normalized for h in history):
                    raise ValueError('Question repeats a rejected candidate')
                with database() as conn:
                    _valid_paper(conn, thread_id)
                    _not_duplicate(conn, thread_id, question.stem)
                # Academic LLM-as-judge evaluation is intentionally deferred.
                _validate_content(question.model_dump(), slot)
                if source_override and not question.source_ids:
                    raise ValueError('The requested chapter question must cite its supplied material')
                if session['requirements']['material_scope'] != 'general' and any(
                    sid not in {src['chunk_id'] for src in sources} for sid in question.source_ids
                ):
                    raise ValueError('Question cited a source outside the retrieved course material')
                data = {**question.model_dump(), 'topic': slot['topic'],
                        'sources': [src for src in sources if src['chunk_id'] in question.source_ids],
                        'grounded': bool(question.source_ids)}
                with database() as conn:
                    _valid_paper(conn, thread_id)
                    changed = conn.execute("UPDATE exam_papers SET candidate=?,candidate_id=?,candidate_slot=?,status='ready',generation_token=NULL,generation_started=NULL,updated_at=? WHERE thread_id=? AND generation_token=? AND status='generating'",
                                           (json.dumps(data), uid(), slot['ordinal'], stamp(), thread_id, token))
                    if changed.rowcount != 1:
                        raise HTTPException(status_code=409, detail='Candidate generation was superseded')
                    _event(conn, thread_id, 'candidate_generated', int((time.monotonic()-started)*1000))
                    if regenerate and paper['candidate']:
                        conn.execute('INSERT INTO exam_candidate_history(id,thread_id,slot,stem,reason,created_at) VALUES(?,?,?,?,?,?)',
                                     (uid(), thread_id, slot['ordinal'], json.loads(paper['candidate'])['stem'], 'regenerated', stamp()))
                    return read(conn, thread_id)
            except (ValueError, generation.ValidationError) as exc:
                last_error = str(exc)[:350]
                feedback = last_error + '\n' + feedback
        raise HTTPException(status_code=422, detail='Could not generate a valid question: ' + last_error)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        with database() as conn:
            _event(conn, thread_id, 'generation_failed', message='Model request timed out')
        raise HTTPException(status_code=504, detail='The model timed out. Your accepted questions are safe; retry generation.') from exc
    except (httpx.RequestError, ConnectionError) as exc:
        with database() as conn:
            _event(conn, thread_id, 'generation_failed', message='Model connection failed')
        raise HTTPException(status_code=503, detail='Cannot reach the model. Check the provider and retry; the paper was saved.') from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Question generation failed for thread %s', thread_id)
        with database() as conn:
            _event(conn, thread_id, 'generation_failed', message='Unexpected generation error')
        raise HTTPException(status_code=502, detail='Question generation failed. Your accepted questions remain saved.') from exc
    finally:
        with database() as conn:
            conn.execute("UPDATE exam_papers SET status='ready',generation_token=NULL,generation_started=NULL,updated_at=? WHERE thread_id=? AND generation_token=? AND status='generating'",
                         (stamp(), thread_id, token))


def cancel_generation(conn: sqlite3.Connection, thread_id: str) -> dict:
    paper, _ = _valid_paper(conn, thread_id, require_ready=True)
    if paper['status'] != 'generating':
        raise HTTPException(status_code=409, detail='No active generation to cancel')
    # Invalidate the in-flight token. A late model response cannot save its result.
    conn.execute("UPDATE exam_papers SET status='ready',generation_token=NULL,generation_started=NULL,updated_at=? WHERE thread_id=?",
                 (stamp(), thread_id))
    _event(conn, thread_id, 'generation_cancelled')
    return read(conn, thread_id)
