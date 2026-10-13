"""Professor-approved deliverables for Agent Mode.

A model can suggest work, never approve it. Assessment execution reuses the
existing Exam Mode state, paper, generation and versioned revision services.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend import exam_builder, exam_state
from backend.marks import to_cents
from backend.agent import academic, research


class ProposedSection(BaseModel):
    topic_id: str = Field(min_length=1, max_length=80)
    kind: Literal['mcq', 'short_answer', 'long_answer', 'problem_solving', 'custom']
    count: int = Field(ge=1, le=100)
    marks_each: float = Field(gt=0, le=100)


class ProposedWork(BaseModel):
    kind: Literal['exam', 'question_bank', 'topic_coverage', 'research_summary', 'assessment_review']
    title: str = Field(min_length=3, max_length=160)
    rationale: str = Field(min_length=12, max_length=2000)
    sections: list[ProposedSection] = Field(default_factory=list, max_length=12)
    exam_type: Literal['quiz', 'midterm', 'final', 'assignment', 'practice', 'custom'] = 'custom'
    # Grounded narrative may cite permitted chunk IDs and previously saved web sources.
    content: str = Field(default='', max_length=24000)
    course_chunk_ids: list[str] = Field(default_factory=list, max_length=30)
    web_source_ids: list[int] = Field(default_factory=list, max_length=30)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_workflows (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        course_id TEXT NOT NULL,
        document_ids TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected')),
        payload TEXT NOT NULL,
        result TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS agent_workflows_thread ON agent_workflows(thread_id, created_at DESC)')


def _hydrate(row) -> dict:
    item = dict(row)
    item['document_ids'] = json.loads(item['document_ids'])
    item['payload'] = json.loads(item['payload'])
    item['result'] = json.loads(item['result']) if item['result'] else None
    return item


def latest(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute('SELECT * FROM agent_workflows WHERE thread_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (thread_id,)).fetchone()
    return _hydrate(row) if row else None


def _validate_evidence(conn: sqlite3.Connection, thread_id: str, course_id: str, ids: list[str], proposal: ProposedWork) -> None:
    view = academic.snapshot(thread_id, course_id, ids)
    topics = {item['id']: item for item in view['topics']}
    if proposal.kind in ('exam', 'question_bank'):
        if not ids:
            raise ValueError('Choose course materials before proposing questions')
        if not proposal.sections:
            raise ValueError('Assessment proposals must include question formats, marks and topic selections')
        for section in proposal.sections:
            if section.topic_id not in topics:
                raise ValueError('Assessment section uses a topic not supported by the selected materials')
        total_cents = sum(section.count * to_cents(section.marks_each) for section in proposal.sections)
        if total_cents > 100_000 or sum(section.count for section in proposal.sections) > exam_builder.MAX_SLOTS:
            raise ValueError('Assessment exceeds the supported question or marks limits')
    elif proposal.sections:
        raise ValueError('Research deliverables cannot reserve assessment slots')
    allowed_chunks = {ref['chunk_id'] for topic in view['topics'] for ref in topic['evidence']}
    if any(chunk not in allowed_chunks for chunk in proposal.course_chunk_ids):
        raise ValueError('Research cites a chunk outside the evidence-backed topic map')
    allowed_web = {item['id'] for item in research.sources(thread_id, course_id, limit=200)}
    if any(source not in allowed_web for source in proposal.web_source_ids):
        raise ValueError('Research cites an unrecognized web source')
    if proposal.kind in ('research_summary', 'topic_coverage', 'assessment_review') and not proposal.content.strip():
        raise ValueError('Research deliverables need substantive content to review')
    if research.policy(thread_id)['mode'] == 'course_only' and proposal.web_source_ids:
        raise ValueError('External sources are disabled for this conversation')


def propose(conn: sqlite3.Connection, thread_id: str, course_id: str, ids: list[str], data: dict) -> dict:
    proposal = ProposedWork.model_validate(data)
    _validate_evidence(conn, thread_id, course_id, ids, proposal)
    existing = latest(conn, thread_id)
    if existing and existing['status'] == 'proposed':
        conn.execute("UPDATE agent_workflows SET status='rejected',updated_at=? WHERE id=?", (time.time(), existing['id']))
    uid = str(uuid.uuid4())
    stamp = time.time()
    conn.execute('''INSERT INTO agent_workflows
        (id,thread_id,course_id,document_ids,kind,status,payload,created_at,updated_at)
        VALUES (?,?,?,?,?,'proposed',?,?,?)''',
        (uid, thread_id, course_id, json.dumps(ids), proposal.kind, proposal.model_dump_json(), stamp, stamp))
    return latest(conn, thread_id)


def decide(conn: sqlite3.Connection, thread_id: str, workflow_id: str, *, approve: bool) -> dict:
    row = conn.execute('SELECT * FROM agent_workflows WHERE id=? AND thread_id=?', (workflow_id, thread_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail='Plan not found')
    request = _hydrate(row)
    if request['status'] != 'proposed':
        raise HTTPException(status_code=409, detail='This plan has already been decided')
    from backend.agent import permissions, state
    current = state.get(conn, thread_id)
    if not current or current['document_ids'] != request['document_ids']:
        raise HTTPException(status_code=409, detail='The course selection changed; investigate again')
    if current['status'] == 'running':
        raise HTTPException(status_code=409, detail='Wait for the agent to finish its current turn')
    result = {'type': 'dismissed'}
    if approve:
        proposal = ProposedWork.model_validate(request['payload'])
        try:
            permissions.selected_documents(conn, request['course_id'], request['document_ids'])
            _validate_evidence(conn, thread_id, request['course_id'], request['document_ids'], proposal)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if proposal.kind in ('exam', 'question_bank'):
            # Explicit approval of the proposal permits preparation only. The
            # professor must separately approve the editable blueprint and
            # each resulting question, exactly as in Exam Mode.
            existing = exam_builder.read(conn, thread_id)
            if existing and existing['status'] in ('generating', 'ready', 'complete'):
                raise HTTPException(status_code=409, detail='An existing paper is already in progress; use its review controls')
            from backend import exam_tools
            exam_state.get_or_create(conn, thread_id, request['course_id'],
                context_summary=exam_tools.summarize_chat_context(conn, thread_id))
            topics = {item['id']: item for item in academic.snapshot(thread_id, request['course_id'], request['document_ids'])['topics']}
            formats = [dict(kind=s.kind, count=s.count, marks_each=s.marks_each) for s in proposal.sections]
            total = sum(s.count * to_cents(s.marks_each) for s in proposal.sections) / 100
            patch = exam_state.RequirementsPatch(exam_type=proposal.exam_type if proposal.kind == 'exam' else 'practice',
                total_marks=total, question_formats=[exam_state.QuestionFormat(**f) for f in formats],
                material_scope='selected')
            exam_state.update(conn, thread_id, patch, document_ids=request['document_ids'])
            exam_state.approve(conn, thread_id)
            paper = exam_builder.prepare(conn, thread_id)
            blueprint = exam_builder.Blueprint.model_validate(paper['blueprint'])
            blueprint.title = proposal.title
            # Let the professor change titles, topics and cognitive demand
            # using the existing paper editor before approving generation.
            for index, section in enumerate(blueprint.sections):
                section.topic = topics[proposal.sections[index].topic_id]['title']
                section.difficulty = 'auto'
            paper = exam_builder.change_blueprint(conn, thread_id, blueprint)
            result = {'type': 'exam_paper', 'status': paper['status'], 'thread_id': thread_id}
        else:
            # Preserve the researched, attributed artifact in the approval record.
            result = {'type': proposal.kind, 'content': proposal.content,
                      'course_chunk_ids': proposal.course_chunk_ids, 'web_source_ids': proposal.web_source_ids}
    conn.execute('UPDATE agent_workflows SET status=?,result=?,updated_at=? WHERE id=?',
                 ('approved' if approve else 'rejected', json.dumps(result), time.time(), workflow_id))
    return latest(conn, thread_id)
