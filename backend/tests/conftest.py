"""Never call real vision providers in tests that do not opt in explicitly."""
import pytest


@pytest.fixture(autouse=True)
def default_offline_vision(monkeypatch):
    monkeypatch.setenv("QUESTGEN_VISION_ENABLED", "false")
