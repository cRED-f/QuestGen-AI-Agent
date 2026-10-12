"""Course-scoped, backend-enforced tools for QuestGen Exam Mode.

Tool implementations are plain functions for deterministic validation and
replay. The model may propose requirement updates, but never approves an exam.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException
from backend import documents, exam_state


QUESTIONS = {
    'faculty_initials': 'What are your faculty initials? (For example, FI.)',
    'university_name': 'What is the full name of your university?',
    'exam_type': 'Which assessment type? Quiz, Midterm, Final, Assignment, Practice, or Custom?',
    'total_marks': 'How many total marks should the paper carry?',
    'difficulty': 'Choose the overall difficulty: Hard++, Complex, Moderate, or Easy.',
    'question_formats': 'How should the marks be divided? Specify question type, number of questions, and marks per question.',
    'material_scope': 'Should I use all course materials, selected uploaded files, or general knowledge?',
    'document_ids': 'Please select at least one ready uploaded material.',
    'marks_distribution': 'The question sections do not add up to the total marks. Please adjust the counts or marks.',
}


def summarize_chat_context(conn: sqlite3.Connection, thread_id: str) -> str:
    """Bounded evidence-preserving summary; never treat chat as approved requirements.

    We quote the relevant user turns rather than inventing or promoting claims.
    Context is advisory and separate from the explicit exam specification.
    """
    rows = conn.execute("""SELECT content FROM messages WHERE thread_id=? AND role='user'
        ORDER BY created_at DESC, rowid DESC LIMIT 24""", (thread_id,)).fetchall()
    keywords = ('exam', 'quiz', 'midterm', 'final', 'course', 'chapter', 'topic',
                'lecture', 'student', 'question', 'marks', 'difficulty', 'syllabus',
                'university', 'faculty', 'assessment')
    messages = [r['content'].strip()[:350] for r in reversed(rows) if r['content'].strip()]
    relevant = [line for line in messages
                if any(word in line.lower() for word in keywords)]
    # Include recent turns too: "BFS and DFS" may matter without containing
    # a generic exam keyword. Retain direct quotes rather than invented facts.
    selected = list(dict.fromkeys([*relevant[-10:], *messages[-6:]]))[-16:]
    return '\n'.join(f'Professor previously said: {line}' for line in selected)[-3200:]


def list_materials(conn: sqlite3.Connection, course_id: str, thread_id: str | None = None) -> list[dict]:
    if thread_id:
        exam_state.log_tool(conn, thread_id, 'list_materials')
    return [dict(row) for row in conn.execute('''SELECT id,filename,status,chunk_count,pages,warning
        FROM documents WHERE course_id=? ORDER BY created_at DESC''', (course_id,))]


def search_materials(course_id: str, query: str, document_ids: list[str] | None) -> list[dict]:
    if not query.strip():
        return []
    return documents.retrieve(course_id, query[:500], 5, document_ids or None)


def save_exam_requirements(conn: sqlite3.Connection, thread_id: str, course_id: str,
                           updates: dict, document_ids: list[str] | None = None) -> dict:
    try:
        patch = exam_state.RequirementsPatch.model_validate(updates)
    except Exception as exc:
        raise HTTPException(status_code=422, detail='Invalid exam requirements') from exc
    if updates.get('material_scope') in ('all_course', 'general') and document_ids is None:
        document_ids = []
    if document_ids is not None:
        try:
            documents.verify_course_documents(conn, course_id, document_ids)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    exam_state.log_tool(conn, thread_id, 'save_exam_requirements')
    return exam_state.update(conn, thread_id, patch, document_ids=document_ids)


def get_exam_requirements(conn: sqlite3.Connection, thread_id: str) -> dict:
    exam_state.log_tool(conn, thread_id, 'get_exam_requirements')
    session = exam_state.read(conn, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail='Exam session not found')
    return session


def validate_exam_requirements(conn: sqlite3.Connection, thread_id: str) -> list[str]:
    exam_state.log_tool(conn, thread_id, 'validate_exam_requirements')
    session = exam_state.read(conn, thread_id)
    return session['missing'] if session else list(exam_state.STEPS)


def ask_professor(conn: sqlite3.Connection, thread_id: str) -> dict:
    exam_state.log_tool(conn, thread_id, 'ask_professor')
    session = exam_state.read(conn, thread_id)
    missing = session['missing'] if session else list(exam_state.STEPS)
    if missing:
        field = missing[0]
        return {'field': field, 'question': QUESTIONS[field]}
    return {'field': None, 'question': 'Your configuration is ready. Review it, then approve to proceed.'}
