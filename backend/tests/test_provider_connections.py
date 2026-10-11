"""UI-managed credentials, model discovery, and safe provider connection behavior."""
import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as module


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTGEN_DB_PATH", str(tmp_path / "data" / "app.sqlite3"))
    monkeypatch.delenv("QUESTGEN_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
                "NINE_ROUTER_API_KEY", "QUESTGEN_OLLAMA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with TestClient(module.app) as c:
        yield c


def test_store_encrypted_keys_reload_and_never_return_them(client, monkeypatch, tmp_path):
    key = "sk-test-only-store-this-secret"
    response = client.put("/api/provider-connections/openai", json={"api_key": key})
    assert response.status_code == 200, response.text
    assert response.json()["key_source"] == "saved"
    assert response.json()["key_configured"] is True
    assert key not in response.text
    assert key not in client.get("/api/provider-connections").text
    assert key not in (tmp_path / "data" / "app.sqlite3").read_bytes().decode("latin1")
    assert (tmp_path / "data" / ".questgen-provider.key").exists()
    assert client.get("/api/provider-connections").json()[1]["provider"] == "openai"
    assert module.model_credentials({"provider": "openai"})[1]["Authorization"] == "Bearer " + key
    # The ciphertext must persist across requests, and an empty key must keep it.
    assert client.put("/api/provider-connections/openai", json={"api_key": ""}).status_code == 200
    assert module.model_credentials({"provider": "openai"})[1]["Authorization"] == "Bearer " + key
    assert client.put("/api/provider-connections/openai", json={"clear_key": True}).json()["key_source"] == "missing"
    assert client.get("/api/models?provider=openai").status_code == 422


def test_environment_keys_continue_to_work_and_saved_override(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-secret")
    initial = client.get("/api/provider-connections").json()
    config = next(x for x in initial if x["provider"] == "openrouter")
    assert config["key_source"] == "environment"
    assert "env-secret" not in str(initial)
    assert module.model_credentials({"provider": "openrouter"})[1]["Authorization"] == "Bearer env-secret"
    client.put("/api/provider-connections/openrouter", json={"api_key": "user-secret"})
    assert module.model_credentials({"provider": "openrouter"})[1]["Authorization"] == "Bearer user-secret"
    client.put("/api/provider-connections/openrouter", json={"clear_key": True})
    assert module.model_credentials({"provider": "openrouter"})[1]["Authorization"] == "Bearer env-secret"


def test_local_api_urls_and_optional_keys(client, monkeypatch):
    response = client.put("/api/provider-connections/9router", json={
        "api_key": "nine-key", "base_url": "http://127.0.0.1:20128/v1/"})
    assert response.status_code == 200
    url, headers = module.model_credentials({"provider": "9router"})
    assert url == "http://127.0.0.1:20128/v1/chat/completions"
    assert headers["Authorization"] == "Bearer nine-key"
    response = client.put("/api/provider-connections/ollama", json={
        "base_url": "http://localhost:11434"})
    assert response.status_code == 200
    url, headers = module.model_credentials({"provider": "ollama"})
    assert url == "http://localhost:11434/v1/chat/completions"
    assert headers == {}
    response = client.put("/api/provider-connections/ollama", json={"api_key": "proxy-key"})
    assert response.status_code == 200
    assert module.model_credentials({"provider": "ollama"})[1]["Authorization"] == "Bearer proxy-key"


@pytest.mark.parametrize("url", [
    "http://example.com:9999/v1", "http://user:pass@localhost:20128/v1",
    "file:///etc/passwd", "http://127.0.0.1:20128/v1/chat/completions",
    "https://example.com/v1?key=x", "ftp://localhost/v1", "not a URL",
])
def test_reject_invalid_or_insecure_gateway_url(client, url):
    result = client.put("/api/provider-connections/9router", json={"base_url": url})
    assert result.status_code == 422, (url, result.text)


def test_reject_unsupported_configuration_and_bad_key(client):
    assert client.put("/api/provider-connections/invalid", json={"api_key": "key"}).status_code == 422
    assert client.put("/api/provider-connections/openai", json={"base_url": "https://example.com"}).status_code == 422
    assert client.put("/api/provider-connections/openai", json={"api_key": "secret key"}).status_code == 422
    assert client.put("/api/provider-connections/openai", json={"api_key": "key", "clear_key": True}).status_code == 422


def test_saved_credentials_are_used_for_native_anthropic_models(client, monkeypatch):
    assert client.put("/api/provider-connections/anthropic", json={"api_key": "anthropic-ui-key"}).status_code == 200
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        def handler(request):
            assert request.headers["x-api-key"] == "anthropic-ui-key"
            assert request.headers["anthropic-version"] == "2023-06-01"
            return httpx.Response(200, json={"data": [{"id": "claude-test", "display_name": "Claude Test"}]})
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    result = client.post("/api/provider-connections/anthropic/test")
    assert result.status_code == 200
    assert result.json() == {"provider": "anthropic", "connected": True, "models_found": 1}
    assert client.get("/api/models?provider=anthropic").json()["models"][0]["id"] == "claude-test"


def test_9router_catalog_uses_saved_gateway_and_key(client, monkeypatch):
    client.put("/api/provider-connections/9router", json={"api_key": "gateway-secret", "base_url": "http://localhost:20128/v1"})
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        def handler(request):
            assert request.url.host == "localhost"
            assert request.url.path == "/v1/models"
            assert request.headers["authorization"] == "Bearer gateway-secret"
            return httpx.Response(200, json={"data": [{"id": "provider/model-xyz"}]})
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    result = client.post("/api/provider-connections/9router/test")
    assert result.status_code == 200
    assert result.json()["models_found"] == 1


def test_connection_failures_hide_provider_response_and_secret(client, monkeypatch):
    client.put("/api/provider-connections/openai", json={"api_key": "super-secret"})
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(
            401, text="private-key=super-secret"))
        return original(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    failed = client.post("/api/provider-connections/openai/test")
    assert failed.status_code == 502
    assert "super-secret" not in failed.text
    assert "private-key" not in failed.text


def test_document_embeddings_use_ui_configured_ollama_proxy(client, monkeypatch):
    from backend import documents
    client.put("/api/provider-connections/ollama", json={
        "api_key": "protected-ollama", "base_url": "https://ollama.example.org"})
    monkeypatch.setenv("QUESTGEN_EMBED_MODEL", "embedding-model")
    original = httpx.Client

    def factory(*args, **kwargs):
        def handler(request):
            assert request.url.host == "ollama.example.org"
            assert request.url.path == "/api/embed"
            assert request.headers["authorization"] == "Bearer protected-ollama"
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(documents.httpx, "Client", factory)
    assert documents.embed_texts(["hello"]) == [[1.0, 0.0]]
