"""SQL-owned session metadata and explicit lifecycle states.

LangGraph owns the continuation checkpoint, while this table enforces exactly
one active request and binds the document allowlist to the existing thread.
"""
import json
import sqlite3
import time


def ensure_tables(conn: sqlite3.Connection) -> None:
    from backend.agent import academic, research, workflows
    academic.ensure_tables(conn)
    research.ensure_tables(conn)
    workflows.ensure_tables(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_sessions (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'ready',
        document_ids TEXT NOT NULL DEFAULT '[]',
        scope_locked INTEGER NOT NULL DEFAULT 0,
        pending_question TEXT,
        activity TEXT NOT NULL DEFAULT '[]',
        updated_at REAL NOT NULL DEFAULT 0
    )""")


def get(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM agent_sessions WHERE thread_id=?", (thread_id,)).fetchone()
    if row is None:
        return None
    entry = dict(row)
    entry['document_ids'] = json.loads(entry['document_ids'])
    entry['activity'] = json.loads(entry['activity'])
    entry['pending_question'] = json.loads(entry['pending_question']) if entry['pending_question'] else None
    return entry


def public(state: dict | None) -> dict:
    if not state:
        return {'status': 'ready', 'pending_question': None, 'activity': [], 'document_ids': []}
    return {field: state[field] for field in ('status', 'pending_question', 'activity', 'document_ids')}


def claim(conn: sqlite3.Connection, thread_id: str, ids: list[str]) -> tuple[dict, bool]:
    """Reserve a run atomically; keep a fixed, explicit document scope."""
    conn.execute("INSERT OR IGNORE INTO agent_sessions(thread_id,updated_at) VALUES (?,?)",
                 (thread_id, time.time()))
    previous = get(conn, thread_id)
    if previous['status'] == 'running' and time.time() - previous['updated_at'] < 180:
        raise ValueError('An agent turn is already running')
    if previous['scope_locked'] and ids != previous['document_ids']:
        raise ValueError('Document selection is locked for this agent conversation. Start a new conversation to change it.')
    conn.execute("UPDATE agent_sessions SET status='running', document_ids=?,scope_locked=1,updated_at=? "
                 "WHERE thread_id=?", (json.dumps(ids), time.time(), thread_id))
    return previous, previous['status'] == 'awaiting_user'


def finish(conn: sqlite3.Connection, thread_id: str, *, question: dict | None,
           activity: list[dict]) -> None:
    conn.execute("UPDATE agent_sessions SET status=?,pending_question=?,activity=?,updated_at=? "
                 "WHERE thread_id=?", ('awaiting_user' if question else 'ready',
                 json.dumps(question) if question else None, json.dumps(activity), time.time(), thread_id))


def fail(conn: sqlite3.Connection, thread_id: str, previous: dict) -> None:
    # Preserve a pending question if resume failed; do not fabricate a completed turn.
    conn.execute("UPDATE agent_sessions SET status=?,pending_question=?,document_ids=?,"
                 "scope_locked=?,updated_at=? WHERE thread_id=?",
                 (previous['status'] if previous['status'] != 'running' else 'ready',
                  json.dumps(previous['pending_question']) if previous['pending_question'] else None,
                  json.dumps(previous['document_ids']), previous['scope_locked'],
                  time.time(), thread_id))
