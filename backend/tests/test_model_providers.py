"""Provider discovery, configuration, and no-silent-fallback regression tests."""

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "catalog.sqlite3"))
    with TestClient(module.app) as c:
        yield c


@pytest.mark.parametrize("provider,endpoint,env,payload,model_id", [
    ("ollama", "/api/tags", None, {"models": [{"name": "qwen3:8b"}]}, "qwen3:8b"),
    ("9router", "/v1/models", None, {"data": [{"id": "9router/smart", "name": "Smart"}]}, "9router/smart"),
    ("openai", "/v1/models", "OPENAI_API_KEY", {"data": [{"id": "gpt-example"}]}, "gpt-example"),
    ("anthropic", "/v1/models", "ANTHROPIC_API_KEY", {"data": [{"id": "claude-example", "display_name": "Claude Example"}]}, "claude-example"),
    ("openrouter", "/api/v1/models", "OPENROUTER_API_KEY", {"data": [{"id": "vendor/model", "name": "Model"}]}, "vendor/model"),
])
def test_provider_discovery(client, monkeypatch, provider, endpoint, env, payload, model_id):
    if env:
        monkeypatch.setenv(env, "test-secret")
    original = httpx.AsyncClient

    def handler(request):
        assert request.url.path == endpoint
        if provider == "anthropic":
            assert request.headers["x-api-key"] == "test-secret"
            assert request.headers["anthropic-version"] == "2023-06-01"
        elif env:
            assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=payload)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
    result = client.get("/api/models", params={"provider": provider})
    assert result.status_code == 200, result.text
    assert result.json()["models"][0]["id"] == model_id


def test_provider_changes_clear_selection_and_require_model(client, monkeypatch):
    response = client.put("/api/settings", json={"provider": "anthropic"})
    assert response.status_code == 200
    assert response.json() == {"provider": "anthropic", "model": ""}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    course = client.get("/api/courses").json()[0]["id"]
    thread = client.post("/api/threads", json={"course_id": course}).json()["id"]
    result = client.post(f"/api/threads/{thread}/stream", json={"content": "hi"})
    assert result.status_code == 422
    assert "Select an available model" in result.json()["detail"]
    assert client.get(f"/api/threads/{thread}/messages").json() == []
    assert client.put("/api/settings", json={"provider": "anthropic", "model": "claude-example"}).status_code == 200


def test_catalog_connection_failures_are_explicit(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = client.get("/api/models", params={"provider": "openai"})
    assert result.status_code == 422
    assert "API key" in result.json()["detail"]
    assert client.get("/api/models", params={"provider": "arbitrary"}).status_code == 422

    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(503, text="internal secret"))
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
    result = client.get("/api/models", params={"provider": "ollama"})
    assert result.status_code == 502
    assert "internal secret" not in result.text
