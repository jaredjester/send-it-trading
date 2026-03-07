"""Alpaca environment bootstrapper.

Canonicalizes credentials so we only define one live + one paper pair
in .env, while older modules that expect ALPACA_API_* vars keep working.

Usage:
    import alpaca_env
    alpaca_env.bootstrap()
"""
from __future__ import annotations

import os
from functools import lru_cache

DEFAULT_LIVE_BASE = "https://api.alpaca.markets"
DEFAULT_PAPER_BASE = "https://paper-api.alpaca.markets"
DEFAULT_DATA_BASE = "https://data.alpaca.markets"


def _setenv(name: str, value: str | None):
    """Set env var if not already set and value is truthy."""
    if name not in os.environ and value:
        os.environ[name] = value

def _bool(val: bool) -> str:
    return "true" if val else "false"

@lru_cache(maxsize=1)
def bootstrap() -> None:
    mode = os.getenv("ALPACA_MODE", "live").strip().lower()
    if mode not in {"live", "paper"}:
        mode = "live"

    live_key = os.getenv("ALPACA_LIVE_KEY")
    live_secret = os.getenv("ALPACA_LIVE_SECRET")
    live_base = os.getenv("ALPACA_LIVE_BASE", DEFAULT_LIVE_BASE)

    paper_key = os.getenv("ALPACA_PAPER_KEY")
    paper_secret = os.getenv("ALPACA_PAPER_SECRET")
    paper_base = os.getenv("ALPACA_PAPER_BASE", DEFAULT_PAPER_BASE)

    data_base = os.getenv("ALPACA_DATA_BASE", DEFAULT_DATA_BASE)

    use_paper = mode == "paper"
    active_key = paper_key if use_paper and paper_key else live_key
    active_secret = paper_secret if use_paper and paper_secret else live_secret
    active_base = paper_base if use_paper else live_base

    # Legacy env aliases (so existing modules keep working without refactor)
    _setenv("ALPACA_API_LIVE_KEY", active_key)
    _setenv("ALPACA_API_KEY", active_key)
    _setenv("APCA_API_KEY_ID", active_key)

    _setenv("ALPACA_API_SECRET", active_secret)
    _setenv("APCA_API_SECRET_KEY", active_secret)

    _setenv("ALPACA_BASE_URL", active_base)
    _setenv("APCA_API_BASE_URL", active_base)
    _setenv("APCA_DATA_URL", data_base)

    _setenv("ALPACA_PAPER", _bool(use_paper))
    _setenv("PAPER_MODE", _bool(use_paper))

    # For dashboards/bot logging
    os.environ["ALPACA_MODE"] = mode
