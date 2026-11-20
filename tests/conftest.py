import pytest


@pytest.fixture(autouse=True)
def disable_paid_embedding_calls(monkeypatch):
    """Unit tests use lexical fallback or injected embedding fakes only."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
