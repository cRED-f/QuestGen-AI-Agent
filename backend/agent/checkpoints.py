"""Persistent per-workspace graph checkpoints. No global in-memory saver."""
from pathlib import Path
import os


def location() -> str:
    from backend.app import DEFAULT_DB
    db = Path(os.environ.get('QUESTGEN_DB_PATH', str(DEFAULT_DB)))
    db.parent.mkdir(parents=True, exist_ok=True)
    return str(db.with_name(db.stem + '-agent-checkpoints.sqlite3'))


def graph_config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': 'questgen-agent:' + thread_id}, 'recursion_limit': 35}


async def purge(thread_ids: list[str]) -> None:
    if not thread_ids or not Path(location()).exists():
        return
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    async with AsyncSqliteSaver.from_conn_string(location()) as saver:
        for thread_id in thread_ids:
            await saver.adelete_thread(graph_config(thread_id)['configurable']['thread_id'])
