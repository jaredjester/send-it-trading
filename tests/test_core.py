"""Tests for core framework components."""
import pytest

from core.alpaca_client import AlpacaClient


def test_alpaca_client_with_empty_config(monkeypatch):
    """AlpacaClient uses provided config when given (no env fallback)."""
    monkeypatch.delenv("ALPACA_API_LIVE_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    client = AlpacaClient(config={"account": {}})
    assert client.api_key in (None, "")
    assert client.api_secret in (None, "")
    assert "alpaca" in client.base_url.lower()
