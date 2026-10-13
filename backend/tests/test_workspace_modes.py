"""Mode routing and user-facing controls remain distinct after Agent Mode rebuild."""
from pathlib import Path
from backend.app import app


def test_chat_agent_and_exam_modes_use_distinct_routes():
    paths = {route.path for route in app.routes}
    assert '/api/threads/{thread_id}/stream' in paths
    assert '/api/threads/{thread_id}/agent/stream' in paths
    assert '/api/threads/{thread_id}/exam/turn' in paths
    # The deleted synchronous endpoint must not return: it blocked on model thinking.
    assert '/api/threads/{thread_id}/agent/turn' not in paths


def test_agent_mode_is_in_chat_workspace_without_extra_search_buttons():
    root = Path(__file__).resolve().parents[2]
    page = (root / 'app/(root)/chat/page.tsx').read_text()
    assert 'switchMode("agent")' in page
    assert '"chat" | "agent" | "exam"' in page
    assert 'agent/stream' in page
    assert '/agent/turn' not in page
    assert 'switchMode("web")' not in page
