"""Local, single-user AI provider connections.

API secrets are encrypted before persisting to SQLite; the Fernet key is a separate
ignored file (or QUESTGEN_CREDENTIAL_ENCRYPTION_KEY). Keep both private. This is
not a public multi-user credentials service: QuestGen deliberately has no auth.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

PROVIDERS = ("9router", "openai", "anthropic", "openrouter", "ollama")
KEY_ENV = {
    "9router": "NINE_ROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "QUESTGEN_OLLAMA_API_KEY",
}
BASE_ENV = {"9router": "QUESTGEN_9ROUTER_URL", "ollama": "QUESTGEN_OLLAMA_URL"}
DEFAULT_BASE = {"9router": "http://127.0.0.1:20128/v1", "ollama": "http://127.0.0.1:11434"}


class ConnectionInput(BaseModel):
    # Omitted / empty key keeps the current key. It is never returned to the UI.
    api_key: str | None = Field(default=None, max_length=2048)
    clear_key: bool = False
    base_url: str | None = Field(default=None, max_length=500)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: str | None):
        if value is not None and any(ord(ch) < 33 or ord(ch) == 127 for ch in value):
            # empty is the only allowed whitespace/empty case
            if value != "":
                raise ValueError("The API key must not contain spaces or control characters")
        return value


def validate_base(provider: str, value: str) -> str:
    if provider not in BASE_ENV:
        raise HTTPException(status_code=422, detail="This provider uses a fixed official API endpoint")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid server URL") from exc
    if (not host or not port and parsed.netloc.endswith(":") or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.scheme not in {"http", "https"}):
        raise HTTPException(status_code=422, detail="Invalid server URL")
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=422, detail="Remote provider URLs must use HTTPS")
    allowed_path = {"9router": {"", "/v1"}, "ollama": {"", "/"}}
    if parsed.path not in allowed_path[provider]:
        raise HTTPException(status_code=422, detail="Use the API base URL, not a request path")
    return value


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS provider_connections (
        provider TEXT PRIMARY KEY,
        encrypted_key TEXT,
        base_url TEXT
    )""")


def _cipher() -> Fernet:
    configured = os.getenv("QUESTGEN_CREDENTIAL_ENCRYPTION_KEY")
    if configured:
        return Fernet(configured.encode("ascii"))
    # Resolve the active test / configured DB location, not a fixed global file.
    from backend.app import DEFAULT_DB
    db_path = Path(os.getenv("QUESTGEN_DB_PATH", str(DEFAULT_DB)))
    key_path = db_path.parent / ".questgen-provider.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with key_path.open("xb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(Fernet.generate_key())
    except FileExistsError:
        pass
    return Fernet(key_path.read_bytes())


def connection(conn: sqlite3.Connection, provider: str) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported provider")
    row = conn.execute("SELECT encrypted_key,base_url FROM provider_connections WHERE provider=?", (provider,)).fetchone()
    saved_key = row["encrypted_key"] if row else None
    env_key = os.getenv(KEY_ENV[provider], "")
    source = "saved" if saved_key else "environment" if env_key else "missing"
    base = (row["base_url"] if row and row["base_url"] else
            os.getenv(BASE_ENV[provider], DEFAULT_BASE[provider]) if provider in BASE_ENV else None)
    return {"provider": provider, "key_configured": source != "missing", "key_source": source,
            "base_url": base, "requires_key": provider not in {"ollama", "9router"}}


def secret(conn: sqlite3.Connection, provider: str) -> str:
    row = conn.execute("SELECT encrypted_key FROM provider_connections WHERE provider=?", (provider,)).fetchone()
    if row and row["encrypted_key"]:
        try:
            return _cipher().decrypt(row["encrypted_key"].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Could not decrypt saved API key. Restore the local encryption key or replace this credential.") from exc
    return os.getenv(KEY_ENV[provider], "")


def save(conn: sqlite3.Connection, provider: str, data: ConnectionInput) -> dict:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported provider")
    if data.clear_key and data.api_key:
        raise HTTPException(status_code=422, detail="Cannot save and clear an API key together")
    current = conn.execute("SELECT encrypted_key,base_url FROM provider_connections WHERE provider=?", (provider,)).fetchone()
    encrypted = current["encrypted_key"] if current else None
    base = current["base_url"] if current else None
    if data.clear_key:
        encrypted = None
    elif data.api_key:
        encrypted = _cipher().encrypt(data.api_key.encode("utf-8")).decode("ascii")
    if data.base_url is not None:
        if provider not in BASE_ENV:
            raise HTTPException(status_code=422, detail="This provider does not have a configurable URL")
        base = validate_base(provider, data.base_url or DEFAULT_BASE[provider])
    conn.execute("""INSERT INTO provider_connections(provider,encrypted_key,base_url) VALUES (?,?,?)
        ON CONFLICT(provider) DO UPDATE SET encrypted_key=excluded.encrypted_key,base_url=excluded.base_url""",
        (provider, encrypted, base))
    return connection(conn, provider)
