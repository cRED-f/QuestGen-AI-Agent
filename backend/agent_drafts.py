"""Durable Agent Mode question drafts and an explicit, lossless Exam Mode handoff.

Agent Mode generates/revises questions; Exam Mode ONLY arranges approved selections
in the existing paper builder and requires final human approval to export.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections import OrderedDict
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend import exam_state, exam_builder, exam_tools, documents
from backend.marks import to_cents

MAX_AGENT_QUESTIONS = 100
MAX_DRAFT_BATCH = 30


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS agent_question_drafts (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        origin_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        position INTEGER NOT NULL,
        selected INTEGER NOT NULL DEFAULT 1 CHECK(selected IN (0,1)),
        content TEXT NOT NULL,
        document_ids TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(thread_id,position)
    );
    CREATE INDEX IF NOT EXISTS agent_question_thread ON agent_question_drafts(thread_id,position);
    CREATE TABLE IF NOT EXISTS agent_exam_handoffs (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        question_ids TEXT NOT NULL,
        conversation TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    ''')


class AgentQuestion(BaseModel):
    kind: str = Field(min_length=1)
    stem: str = Field(min_length=5, max_length=2500)
    options: list[str] = Field(default_factory=list, max_length=4)
    correct_answer: str = Field(min_length=1, max_length=3000)
    explanation: str = Field(min_length=1, max_length=2500)
    marks: float = Field(default=1, gt=0, le=100)
    topic: str = Field(default='Course concepts', min_length=2, max_length=200)
    difficulty: str = Field(default='auto')

    def validate_content(self) -> dict:
        if self.kind not in exam_state.FORMATS:
            raise ValueError('Unsupported question type')
        if self.difficulty not in (*exam_state.DIFFICULTIES, 'auto'):
            raise ValueError('Unsupported difficulty')
        to_cents(self.marks)
        return {**exam_builder._validate_content(self.model_dump(), {'kind': self.kind}),
                'kind': self.kind, 'marks': self.marks, 'topic': self.topic,
                'difficulty': self.difficulty}


def _load(row: sqlite3.Row) -> dict:
    data = dict(row)
    data['content'] = json.loads(data['content'])
    data['document_ids'] = json.loads(data['document_ids'])
    data['selected'] = bool(data['selected'])
    return data


def list_questions(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    return [_load(row) for row in conn.execute(
        'SELECT * FROM agent_question_drafts WHERE thread_id=? ORDER BY position', (thread_id,))]


def save_batch(conn: sqlite3.Connection, thread_id: str, questions: list[dict],
               origin_message_id: str | None, document_ids: list[str] | None) -> list[dict]:
    if not 1 <= len(questions) <= MAX_DRAFT_BATCH:
        raise HTTPException(422, f'Provide 1–{MAX_DRAFT_BATCH} questions in a batch')
    count = conn.execute('SELECT COUNT(*) FROM agent_question_drafts WHERE thread_id=?', (thread_id,)).fetchone()[0]
    if count + len(questions) > MAX_AGENT_QUESTIONS:
        raise HTTPException(409, f'At most {MAX_AGENT_QUESTIONS} draft questions are allowed per conversation')
    validated = [AgentQuestion.model_validate(item).validate_content() for item in questions]
    seen = {re.sub(r'\s+', ' ', row[0]).casefold() for row in ((q['stem'],) for q in validated)}
    if len(seen) != len(validated):
        raise HTTPException(422, 'The batch contains duplicate questions')
    existing = {re.sub(r'\s+', ' ', item['content']['stem']).strip().casefold()
                for item in list_questions(conn, thread_id)}
    if any(re.sub(r'\s+', ' ', q['stem']).strip().casefold() in existing for q in validated):
        raise HTTPException(409, 'Question already exists; revise it instead of drafting a duplicate')
    next_pos = conn.execute('SELECT COALESCE(MAX(position),0) FROM agent_question_drafts WHERE thread_id=?',
                            (thread_id,)).fetchone()[0]
    timestamp = time.time()
    output = []
    for index, content in enumerate(validated, next_pos + 1):
        draft_id = str(uuid.uuid4())
        conn.execute('''INSERT INTO agent_question_drafts(id,thread_id,origin_message_id,position,content,document_ids,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)''', (draft_id, thread_id, origin_message_id, index,
                        json.dumps(content), json.dumps(document_ids or []), timestamp, timestamp))
        output.append({'id': draft_id, 'number': index, 'stem': content['stem'], 'kind': content['kind']})
    return output


def update_question(conn: sqlite3.Connection, thread_id: str, draft_id: str, *,
                    selected: bool | None = None, question: dict | None = None,
                    expected_version: int | None = None) -> dict:
    row = conn.execute('SELECT * FROM agent_question_drafts WHERE id=? AND thread_id=?',
                       (draft_id, thread_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Agent draft not found in this conversation')
    if expected_version is not None and row['version'] != expected_version:
        raise HTTPException(409, 'Question changed; reload before editing')
    content = AgentQuestion.model_validate(question).validate_content() if question is not None else json.loads(row['content'])
    if question is not None:
        norm = re.sub(r'\s+', ' ', content['stem']).strip().casefold()
        if any(re.sub(r'\s+', ' ', other['content']['stem']).strip().casefold() == norm
               for other in list_questions(conn, thread_id) if other['id'] != draft_id):
            raise HTTPException(409, 'Duplicate question')
    chosen = int(bool(selected)) if selected is not None else row['selected']
    conn.execute('UPDATE agent_question_drafts SET content=?,selected=?,version=version+1,updated_at=? WHERE id=?',
                 (json.dumps(content), chosen, time.time(), draft_id))
    return _load(conn.execute('SELECT * FROM agent_question_drafts WHERE id=?', (draft_id,)).fetchone())


def remove_question(conn: sqlite3.Connection, thread_id: str, draft_id: str) -> None:
    if not conn.execute('DELETE FROM agent_question_drafts WHERE id=? AND thread_id=?',
                        (draft_id, thread_id)).rowcount:
        raise HTTPException(404, 'Agent draft not found in this conversation')


def discard_from_turn(conn: sqlite3.Connection, thread_id: str, user_id: str) -> None:
    """Editing/regenerating removes drafts authored by the invalidated turn."""
    conn.execute('DELETE FROM agent_question_drafts WHERE thread_id=? AND origin_message_id=?',
                 (thread_id, user_id))
    # A previous print handoff cannot silently claim it still represents edits.
    conn.execute('DELETE FROM agent_exam_handoffs WHERE thread_id=?', (thread_id,))


def _context(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    # Preserve the full conversation as a durable handoff snapshot; original
    # messages and source references also remain in the same thread.
    return [dict(row) for row in conn.execute(
        'SELECT id,role,mode,content FROM messages WHERE thread_id=? ORDER BY rowid', (thread_id,))]


def read_handoff(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute('SELECT * FROM agent_exam_handoffs WHERE thread_id=?', (thread_id,)).fetchone()
    if row is None:
        return None
    return {'question_ids': json.loads(row['question_ids']),
            'conversation': json.loads(row['conversation']), 'created_at': row['created_at']}


def import_selected(conn: sqlite3.Connection, thread_id: str, question_ids: list[str], *, replace: bool = False) -> dict:
    """Explicit professor action: transfer exact selected draft contents without an LLM call.

    Reuse QuestGen's exam session, blueprint, accepted question records, version
    history, preview and human-gated export. No second generation pipeline.
    """
    thread = conn.execute('SELECT * FROM threads WHERE id=?', (thread_id,)).fetchone()
    if thread is None:
        raise HTTPException(404, 'Conversation not found')
    available = {item['id']: item for item in list_questions(conn, thread_id)}
    if not question_ids or len(question_ids) != len(set(question_ids)) or len(question_ids) > exam_builder.MAX_SLOTS:
        raise HTTPException(422, 'Choose between 1 and 100 distinct questions')
    if any(qid not in available or not available[qid]['selected'] for qid in question_ids):
        raise HTTPException(409, 'Only explicitly selected Agent Mode questions can be imported')
    existing = exam_builder.read(conn, thread_id)
    if existing and not replace:
        raise HTTPException(409, 'An exam paper already exists. Confirm replacement before importing selected questions')
    if existing and existing['status'] == 'generating':
        raise HTTPException(409, 'Wait for the current generation to finish before replacing the paper')
    draft_list = [available[qid] for qid in question_ids]
    groups: OrderedDict[tuple[str, int], list[dict]] = OrderedDict()
    for item in draft_list:
        content = item['content']
        key = (content['kind'], to_cents(content['marks']))
        groups.setdefault(key, []).append(item)
    if len(groups) > 12:
        raise HTTPException(422, 'Paper supports at most 12 question type/marks sections')
    total = sum(to_cents(q['content']['marks']) for q in draft_list) / 100
    if total > 1000:
        raise HTTPException(422, 'Selected questions exceed the maximum of 1000 marks')
    selected_documents = list(dict.fromkeys(doc_id for q in draft_list for doc_id in q['document_ids']))
    # Only source-scoped, ready material IDs can influence imported scope.
    if selected_documents:
        try:
            documents.verify_course_documents(conn, thread['course_id'], selected_documents)
        except ValueError as exc:
            raise HTTPException(409, 'Some selected source files are no longer available') from exc
    prior = exam_state.read(conn, thread_id)
    old_requirements = (prior or {}).get('requirements', {})
    formats = [{'kind': kind, 'count': len(items), 'marks_each': cents / 100}
               for (kind, cents), items in groups.items()]
    req = {'exam_type': old_requirements.get('exam_type') or 'custom',
           'total_marks': total, 'question_formats': formats,
           'material_scope': 'selected' if selected_documents else 'general'}
    for name in ('faculty_initials', 'university_name'):
        if old_requirements.get(name):
            req[name] = old_requirements[name]
    snapshot = _context(conn, thread_id)
    session = exam_state.get_or_create(conn, thread_id, thread['course_id'],
                                        context_summary=exam_tools.summarize_chat_context(conn, thread_id))
    session = exam_state.update(conn, thread_id, exam_state.RequirementsPatch.model_validate(req),
                                document_ids=selected_documents)
    if session['missing']:
        raise HTTPException(422, 'Selected question distribution cannot form a valid paper: ' + ', '.join(session['missing']))
    # The import action explicitly accepts the distribution and selected questions;
    # final paper approval remains a separate action before PDF/DOCX export.
    session = exam_state.approve(conn, thread_id)
    sections = []
    for idx, ((kind, cents), items) in enumerate(groups.items(), 1):
        topics = list(dict.fromkeys(item['content']['topic'] for item in items))
        sections.append(exam_builder.Section(id=f'agent-section-{idx}', kind=kind,
                                             count=len(items), marks_each=cents / 100,
                                             topic=topics[0] if len(topics) == 1 else 'Selected topics', difficulty='auto'))
    blueprint = exam_builder.Blueprint(title=f"{old_requirements.get('university_name') or thread['title'] or 'QuestGen'} — Question Paper",
                                       instructions='Answer all questions.', sections=sections)
    exam_builder._validate_blueprint(blueprint, session['requirements'])
    timestamp = time.time()
    # Explicit overwrite only after all input and existing-paper checks passed.
    if existing:
        conn.execute('DELETE FROM exam_papers WHERE thread_id=?', (thread_id,))
    conn.execute('''INSERT INTO exam_papers(thread_id,status,blueprint,session_signature,material_ids,created_at,updated_at)
                    VALUES (?,'complete',?,?,?,?,?)''',
                 (thread_id, blueprint.model_dump_json(), exam_builder.signature(session),
                  json.dumps(selected_documents), timestamp, timestamp))
    slot = 0
    for section, items in zip(sections, groups.values()):
        for item in items:
            slot += 1
            content = {**item['content'], 'ordinal': slot, 'section_id': section.id}
            content.update(exam_builder._validate_content(content, {'kind': section.kind}))
            qid = str(uuid.uuid4())
            conn.execute('''INSERT INTO exam_paper_questions
                (id,thread_id,slot,position,content,version,created_at,updated_at)
                VALUES (?,?,?,?,?,1,?,?)''', (qid, thread_id, slot, slot, json.dumps(content), timestamp, timestamp))
            exam_builder._save_version(conn, qid, 1, content, 'imported_from_agent')
    conn.execute('''INSERT INTO agent_exam_handoffs(thread_id,question_ids,conversation,created_at)
        VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET question_ids=excluded.question_ids,
        conversation=excluded.conversation,created_at=excluded.created_at''',
        (thread_id, json.dumps(question_ids), json.dumps(snapshot), timestamp))
    return {'session': session, 'paper': exam_builder.read(conn, thread_id),
            'handoff': read_handoff(conn, thread_id)}
