"""Regression tests for provider success responses containing no usable JSON."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend import generation


def stub_provider(monkeypatch, responses):
    """Intercept only HTTP requests; never contact a real model provider."""
    requests = []
    responses = iter(responses)

    def handler(request):
        requests.append(json.loads(request.content))
        status, data = next(responses)
        return httpx.Response(status, json=data)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(generation, "provider_details", lambda settings: ("http://local-model/v1/chat/completions", {}))
    monkeypatch.setattr(generation.httpx, "AsyncClient",
                        lambda *args, **kwargs: original_client(*args, transport=transport, **kwargs))
    return requests


def choice(content, *, finish_reason="stop", usage=None, parsed=None, reasoning=None):
    message = {"role": "assistant", "content": content}
    if parsed is not None:
        message["parsed"] = parsed
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"finish_reason": finish_reason, "message": message}],
            "usage": usage or {}}


def invoke(provider="ollama", max_tokens=4096):
    return asyncio.run(generation.model_json(
        {"provider": provider, "model": "test-model"}, "Output a question", {"topic": "graphs"},
        max_tokens=max_tokens))


def test_ollama_uses_json_mode_and_disables_thinking(monkeypatch):
    requests = stub_provider(monkeypatch, [(200, choice('{"stem":"What is a graph?"}'))])
    assert invoke() == {"stem": "What is a graph?"}
    assert requests[0]["max_tokens"] == 4096
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["stream"] is False


def test_ollama_falls_back_for_old_server(monkeypatch):
    requests = stub_provider(monkeypatch, [(400, {"error": "unsupported parameter"}),
                                         (200, choice('```json\n{"valid":true}\n```'))])
    assert invoke() == {"valid": True}
    assert len(requests) == 2
    assert "reasoning_effort" not in requests[1]
    assert "response_format" not in requests[1]


def test_non_ollama_gateway_gets_no_universal_json_mode(monkeypatch):
    requests = stub_provider(monkeypatch, [(200, choice('{"valid":true}'))])
    assert invoke("openrouter") == {"valid": True}
    assert "response_format" not in requests[0]
    assert "reasoning_effort" not in requests[0]


def test_json_in_content_parts_and_wrapped_in_text(monkeypatch):
    stub_provider(monkeypatch, [(200, choice([{"type": "text", "text": 'Answer:\n{"valid": '},
                                              {"type": "text", "text": 'true}'}]))])
    assert invoke() == {"valid": True}


def test_structured_parsed_object(monkeypatch):
    stub_provider(monkeypatch, [(200, choice(None, parsed={"valid": True}))])
    assert invoke() == {"valid": True}


def test_thinking_only_never_used_as_final_answer(monkeypatch):
    stub_provider(monkeypatch, [(200, choice(None, reasoning='{"stem":"private thoughts"}'))])
    with pytest.raises(ValueError, match="reasoning but no final JSON") as err:
        invoke()
    assert "private thoughts" not in str(err.value)


def test_exhausted_budget_is_diagnostic_not_generic(monkeypatch):
    stub_provider(monkeypatch, [(200, choice("", finish_reason="length",
                                         usage={"completion_tokens_details": {"reasoning_tokens": 4096}}))])
    with pytest.raises(ValueError, match="output token limit"):
        invoke()


def test_refusal_gives_safe_error(monkeypatch):
    stub_provider(monkeypatch, [(200, choice(None, finish_reason="content_filter"))])
    with pytest.raises(ValueError, match="refused"):
        invoke()


def test_partial_json_never_fabricated(monkeypatch):
    stub_provider(monkeypatch, [(200, choice('{"correct_answer": "A"', finish_reason="length"))])
    with pytest.raises(ValueError, match="incomplete JSON"):
        invoke()


def test_retry_increases_budget_after_truncation(monkeypatch):
    budgets = []

    async def fake_json(settings, system, request, *, max_tokens=4096):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            raise ValueError("Model exhausted its output token limit before returning JSON")
        return {"stem": "Which graph algorithm visits nodes breadth first?",
                "options": ["BFS", "DFS", "Prim", "Kruskal"], "correct_answer": "BFS",
                "explanation": "BFS uses a queue and explores nodes one level at a time.",
                "source_ids": []}

    monkeypatch.setattr(generation, "model_json", fake_json)
    plan = generation.QuestionPlan(topic="graph algorithms", kind="mcq", difficulty="medium",
                                   bloom_level="apply", marks=5)
    with pytest.raises(ValueError, match="output token limit") as err:
        asyncio.run(generation.generate_question({"provider": "ollama"}, plan, []))
    candidate = asyncio.run(generation.generate_question(
        {"provider": "ollama"}, plan, [], str(err.value)))
    assert candidate.correct_answer == "BFS"
    assert budgets == [4096, 8192]
