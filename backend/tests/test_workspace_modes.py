"""Regression guard for the intentionally retired Agent Mode implementation."""
from pathlib import Path

from backend.app import app


def test_only_current_workspace_mode_routes_are_exposed():
    paths = {route.path for route in app.routes}
    assert not any('/agent/' in path for path in paths)
    assert '/api/threads/{thread_id}/stream' in paths
    assert '/api/threads/{thread_id}/exam/turn' in paths


def test_deleted_agent_mode_stays_out_of_frontend():
    root = Path(__file__).resolve().parents[2]
    page = (root / 'app/(root)/chat/page.tsx').read_text()
    assert 'switchMode("agent")' not in page
    assert '/agent/turn' not in page
    assert '"chat" | "exam"' in page
    assert 'return saved === "exam" ? "exam" : "chat"' in page
