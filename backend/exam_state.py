"""Durable, thread-scoped Exam Mode requirements and human approval gate."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.marks import allocated_cents, to_cents


EXAM_TYPES = ('quiz', 'midterm', 'final', 'assignment', 'practice', 'custom')
DIFFICULTIES = ('hard++', 'complex', 'moderate', 'easy')
FORMATS = ('mcq', 'short_answer', 'long_answer', 'problem_solving', 'custom')
SCOPE = ('all_course', 'selected', 'general')
# Complexity, university, initials are optional; ask for them only if they matter.
STEPS = ('exam_type', 'total_marks', 'question_formats', 'material_scope')


class QuestionFormat(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    count: int = Field(ge=1, le=100)
    marks_each: float = Field(ge=0.01, le=100)

    @field_validator('marks_each')
    @classmethod
    def validate_marks_each(cls, value: float) -> float:
        to_cents(value)
        return value

    @field_validator('kind')
    @classmethod
    def validate_kind(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in FORMATS:
            raise ValueError('Unsupported question format')
        return name


class RequirementsPatch(BaseModel):
    faculty_initials: str | None = Field(default=None, min_length=1, max_length=20)
    university_name: str | None = Field(default=None, min_length=2, max_length=160)
    exam_type: str | None = None
    total_marks: float | None = Field(default=None, ge=0.01, le=1000)
    difficulty: str | None = None
    question_formats: list[QuestionFormat] | None = Field(default=None, max_length=12)
    material_scope: str | None = None
    topic_scope: str | None = Field(default=None, min_length=2, max_length=200)

    @field_validator('total_marks')
    @classmethod
    def validate_total_marks(cls, value: float | None) -> float | None:
        if value is not None:
            to_cents(value, max_marks=1000)
        return value

    @field_validator('exam_type')
    @classmethod
    def validate_exam_type(cls, value: str | None) -> str | None:
        if value is not None and value not in EXAM_TYPES:
            raise ValueError('Unsupported exam type')
        return value

    @field_validator('difficulty')
    @classmethod
    def validate_difficulty(cls, value: str | None) -> str | None:
        if value is not None and value not in DIFFICULTIES:
            raise ValueError('Unsupported difficulty')
        return value

    @field_validator('material_scope')
    @classmethod
    def validate_scope(cls, value: str | None) -> str | None:
        if value is not None and value not in SCOPE:
            raise ValueError('Unsupported material scope')
        return value

    @field_validator('faculty_initials', 'university_name', 'topic_scope')
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = ' '.join(value.strip().split())
            if not value:
                raise ValueError('This field is required')
        return value


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS exam_sessions (
            thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
            course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'collecting',
            requirements TEXT NOT NULL DEFAULT '{}',
            document_ids TEXT NOT NULL DEFAULT '[]',
            context_summary TEXT NOT NULL DEFAULT '',
            approved_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            next_question TEXT,
            pending_confirmation TEXT
        );
        CREATE TABLE IF NOT EXISTS exam_tool_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            created_at REAL NOT NULL
        );
    ''')
    if 'next_question' not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_sessions)')}:
        conn.execute('ALTER TABLE exam_sessions ADD COLUMN next_question TEXT')
    if 'pending_confirmation' not in {r['name'] for r in conn.execute('PRAGMA table_info(exam_sessions)')}:
        conn.execute('ALTER TABLE exam_sessions ADD COLUMN pending_confirmation TEXT')


def read(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute('SELECT * FROM exam_sessions WHERE thread_id=?', (thread_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['requirements'] = json.loads(result['requirements'])
    result['document_ids'] = json.loads(result['document_ids'])
    result['pending_confirmation'] = (json.loads(result['pending_confirmation'])
                                      if result.get('pending_confirmation') else None)
    result['missing'] = missing(result['requirements'], result['document_ids'])
    result['ready_for_approval'] = not result['missing']
    return result


def get_or_create(conn: sqlite3.Connection, thread_id: str, course_id: str, *, context_summary: str = '') -> dict:
    if not conn.execute('SELECT 1 FROM threads WHERE id=? AND course_id=?',
                        (thread_id, course_id)).fetchone():
        raise HTTPException(status_code=404, detail='Conversation not found in course')
    stamp = time.time()
    conn.execute('''INSERT OR IGNORE INTO exam_sessions
        (thread_id, course_id, status, requirements, document_ids, context_summary, created_at, updated_at)
        VALUES (?,?, 'collecting', '{}','[]',?,?,?)''', (thread_id, course_id, context_summary, stamp, stamp))
    return read(conn, thread_id)  # type: ignore[return-value]


def missing(requirements: dict, document_ids: list[str]) -> list[str]:
    result = [key for key in STEPS if not requirements.get(key)]
    if requirements.get('material_scope') == 'selected' and not document_ids:
        result.append('document_ids')
    formats = requirements.get('question_formats') or []
    total = requirements.get('total_marks')
    if formats and total is not None:
        allocated = allocated_cents(formats)
        if allocated != to_cents(total, max_marks=1000):
            result.append('marks_distribution')
    return result


def refresh_context(conn: sqlite3.Connection, thread_id: str, summary: str) -> dict:
    session = read(conn, thread_id)
    if not session:
        raise HTTPException(status_code=404, detail='Exam session not found')
    if session['context_summary'] != summary:
        conn.execute('UPDATE exam_sessions SET context_summary=?, updated_at=? WHERE thread_id=?',
                     (summary, time.time(), thread_id))
    return read(conn, thread_id)  # type: ignore[return-value]


def update(conn: sqlite3.Connection, thread_id: str, patch: RequirementsPatch,
           *, document_ids: list[str] | None = None) -> dict:
    session = read(conn, thread_id)
    if not session:
        raise HTTPException(status_code=404, detail='Exam session not found')
    if session['status'] == 'approved':
        # Any material change invalidates approval, including document selection.
        status = 'collecting'
    else:
        status = session['status']
    changed = patch.model_dump(exclude_unset=True)
    # A structured patch updates only the provided fields. Do not silently
    # reinterpret unsupported text or allow LLM-supplied approval changes.
    merged = {**session['requirements'], **changed}
    merged = {key: value for key, value in merged.items() if value is not None}
    if document_ids is None:
        document_ids = session['document_ids']
    conn.execute('''UPDATE exam_sessions SET requirements=?, document_ids=?, status=?,
        approved_at=NULL, next_question=NULL, pending_confirmation=NULL, updated_at=? WHERE thread_id=?''',
        (json.dumps(merged), json.dumps(document_ids),
         'awaiting_approval' if not missing(merged, document_ids) else 'collecting',
         time.time(), thread_id))
    return read(conn, thread_id)  # type: ignore[return-value]


def approve(conn: sqlite3.Connection, thread_id: str) -> dict:
    session = read(conn, thread_id)
    if not session:
        raise HTTPException(status_code=404, detail='Exam session not found')
    if session['missing']:
        raise HTTPException(status_code=409, detail='Complete the requirements before approval: ' + ', '.join(session['missing']))
    stamp = time.time()
    conn.execute("UPDATE exam_sessions SET status='approved', approved_at=?, pending_confirmation=NULL, "
                 "updated_at=? WHERE thread_id=?", (stamp, stamp, thread_id))
    return read(conn, thread_id)  # type: ignore[return-value]


def require_approval(conn: sqlite3.Connection, thread_id: str, *, document_ids: list[str] | None = None) -> dict:
    session = read(conn, thread_id)
    if not session or session['status'] != 'approved' or session['missing']:
        raise HTTPException(status_code=409, detail='Complete and approve Exam Mode configuration before generating questions')
    if session['document_ids']:
        found = {row['id'] for row in conn.execute('''SELECT id FROM documents WHERE course_id=? AND status='ready' ''' +
            'AND id IN (' + ','.join('?' for _ in session['document_ids']) + ')',
            (session['course_id'], *session['document_ids']))}
        if found != set(session['document_ids']):
            raise HTTPException(status_code=409, detail='Approved materials changed; reselect and approve again')
    if document_ids is not None and set(document_ids) != set(session['document_ids']):
        # Explicit approved scope cannot be silently overridden by old endpoints.
        raise HTTPException(status_code=409, detail='Selected materials differ from approved exam configuration')
    return session


def log_tool(conn: sqlite3.Connection, thread_id: str, name: str) -> None:
    # Never persist full model input, extracted secret, or source text to telemetry.
    conn.execute('INSERT INTO exam_tool_events(thread_id,tool_name,created_at) VALUES(?,?,?)',
                 (thread_id, name, time.time()))


def set_next_question(conn: sqlite3.Connection, thread_id: str, question: str) -> None:
    conn.execute('UPDATE exam_sessions SET next_question=? WHERE thread_id=?',
                 (question[:300],thread_id))

def set_pending_confirmation(conn: sqlite3.Connection, thread_id: str, payload: dict | None) -> None:
    """Hold an extracted-but-unconfirmed requirement set until the professor decides."""
    conn.execute('UPDATE exam_sessions SET pending_confirmation=?, updated_at=? WHERE thread_id=?',
                 (json.dumps(payload) if payload else None, time.time(), thread_id))
