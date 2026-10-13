"""Persistent, versioned consultation plans for Agent Mode question drafting.

The LLM may *propose* an exam plan but cannot approve its own proposal.
Approval is an explicit user API action tied to the current plan version.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import HTTPException


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute('''CREATE TABLE IF NOT EXISTS agent_consultation_plans (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        origin_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        plan_json TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','approved')),
        updated_at REAL NOT NULL
    )''')


def get_plan(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = conn.execute('SELECT * FROM agent_consultation_plans WHERE thread_id=?', (thread_id,)).fetchone()
    if not row:
        return None
    return {'thread_id': thread_id, 'origin_message_id': row['origin_message_id'],
            'plan': json.loads(row['plan_json']), 'version': row['version'],
            'status': row['status'], 'updated_at': row['updated_at']}


def propose(conn: sqlite3.Connection, thread_id: str, origin_message_id: str | None,
            plan: dict[str, Any]) -> dict[str, Any]:
    summary = str(plan.get('summary') or '').strip()
    if not (10 <= len(summary) <= 4000):
        raise ValueError('Plan summary must describe the proposed assessment')
    raw_details = plan.get('details') or []
    if not isinstance(raw_details, list) or len(raw_details) > 30:
        raise ValueError('Plan details must be a list of at most 30 decisions')
    details = []
    for item in raw_details:
        if not isinstance(item, dict):
            raise ValueError('Each plan detail requires a label and value')
        label, value = str(item.get('label') or '').strip(), str(item.get('value') or '').strip()
        if not label or not value or len(label) > 120 or len(value) > 1000:
            raise ValueError('Invalid plan detail')
        details.append({'label': label, 'value': value})
    value = {'summary': summary, 'details': details}
    existing = get_plan(conn, thread_id)
    version = (existing['version'] + 1) if existing else 1
    conn.execute('''INSERT INTO agent_consultation_plans
        (thread_id,origin_message_id,plan_json,version,status,updated_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET
        origin_message_id=excluded.origin_message_id, plan_json=excluded.plan_json,
        version=excluded.version, status='pending', updated_at=excluded.updated_at''',
        (thread_id, origin_message_id, json.dumps(value, ensure_ascii=False), version, 'pending', time.time()))
    return get_plan(conn, thread_id)


def approve(conn: sqlite3.Connection, thread_id: str, version: int) -> dict[str, Any]:
    current = get_plan(conn, thread_id)
    if current is None:
        raise HTTPException(status_code=409, detail='Discuss your questions and agree on a draft plan first')
    if current['version'] != version or current['status'] != 'pending':
        raise HTTPException(status_code=409, detail='Plan changed or is already approved; reload before confirming')
    conn.execute("UPDATE agent_consultation_plans SET status='approved',updated_at=? WHERE thread_id=? AND version=? AND status='pending'",
                 (time.time(), thread_id, version))
    return get_plan(conn, thread_id)


def can_draft(conn: sqlite3.Connection, thread_id: str) -> bool:
    plan = get_plan(conn, thread_id)
    return bool(plan and plan['status'] == 'approved')


def invalidate_if_superseded(conn: sqlite3.Connection, thread_id: str, removed_message_ids: set[str]) -> None:
    current = get_plan(conn, thread_id)
    if current and current['origin_message_id'] in removed_message_ids:
        conn.execute('DELETE FROM agent_consultation_plans WHERE thread_id=?', (thread_id,))
