"""Pytest fixtures and configuration for send-it-trading tests."""
import os
import sys
from pathlib import Path

import pytest

# Add project root to path
ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    """Ensure tests never use real API credentials."""
    monkeypatch.delenv("ALPACA_API_LIVE_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)


@pytest.fixture
def mock_config(tmp_path):
    """Provide a minimal config for unit tests."""
    config = {
        "account": {
            "alpaca_api_key": "test_key",
            "alpaca_secret_key": "test_secret",
            "alpaca_base_url": "https://paper-api.alpaca.markets",
            "alpaca_data_url": "https://data.alpaca.markets",
            "data_feed": "iex",
        },
        "portfolio": {"max_position_pct": 0.2},
        "risk": {},
        "execution_gate": {"rl_state_path": str(tmp_path / "q_state.json")},
        "benchmark": {"state_file": str(tmp_path / "benchmark.json")},
        "logging": {"level": "INFO", "log_file": str(tmp_path / "test.log")},
        "data": {"cache_ttl_seconds": 300},
    }
    config_path = tmp_path / "master_config.json"
    import json
    config_path.write_text(json.dumps(config, indent=2))
    return config_path
