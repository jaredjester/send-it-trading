#!/usr/bin/env python3
"""
Dynamic Config — single source of truth for all orchestrator parameters.

All values live in evaluation/live_config.json.
The overnight optimizer continuously improves these values.
The RL episode bridge writes back rl_action + multipliers after each day.
No hardcoded constants anywhere — everything flows through cfg().

Usage:
    from core.dynamic_config import cfg, cfg_set, cfg_all

    threshold = cfg("min_score_threshold")    # → 63.0
    cfg_set("rl_action", "aggressive_buy")    # write back to disk
"""

import json
import time
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("dynamic_config")

_CONFIG_PATH = Path(__file__).parent.parent / "evaluation" / "live_config.json"

# ──────────────────────────────────────────────────────────────────────────────
# DEFAULTS — used when a key is absent from live_config.json
# The optimizer overwrites the trading params; RL bridge overwrites rl_* params.
# These defaults are conservative intentionally.
# ──────────────────────────────────────────────────────────────────────────────
DEFAULTS: dict = {
    # Trading thresholds (overwritten by overnight_optimizer.py)
    "min_score_threshold":  63.0,
    "max_position_pct":     0.12,
    "stop_loss_pct":       -0.06,
    "live_sharpe_haircut":  0.55,
    "max_total_exposure":   0.85,
    "min_cash_reserve":    75.0,
    "min_trade_notional":  10.0,
    "max_trades_per_cycle": 3,
    "min_position_value":   1.0,

    # IC / signal quality (overwritten by alpha_tracker)
    "ic_kill_threshold":    0.03,
    "ic_strong_threshold":  0.15,

    # Zombie cleanup
    "zombie_loss_threshold": -0.50,

    # Untradeable symbols (delisted / frozen)
    "untradeable_symbols": ["AVGR", "BGXXQ", "MOTS"],

    # Fallback watchlist when all scanners return empty
    "watchlist": [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA",
        "AMZN", "META", "AMD", "COIN", "MARA", "GOOGL",
    ],

    # RL episode outputs — written by episode_bridge after each trading day.
    # Orchestrator reads these to scale aggressiveness based on recent performance.
    "rl_action":           "hold",   # Q-recommended action for this session
    "rl_trade_multiplier":  1.0,     # scales MAX_TRADES_PER_CYCLE (0=no trades, 1.5=push)
    "rl_size_multiplier":   1.0,     # scales max_position_pct
    "rl_last_episode_return": 0.0,
    "rl_updated_at":        None,
}

# ──────────────────────────────────────────────────────────────────────────────
# Cache — re-read from disk at most every 60 seconds
# ──────────────────────────────────────────────────────────────────────────────
_TTL = 60.0
_cache: dict = {"data": {}, "ts": 0.0}


def _load() -> dict:
    now = time.monotonic()
    if now - _cache["ts"] < _TTL:
        return _cache["data"]
    try:
        raw = _CONFIG_PATH.read_text()
        data = json.loads(raw)
        _cache["data"] = data
        _cache["ts"] = now
        return data
    except Exception as e:
        logger.debug(f"Config read failed ({e}) — using stale/defaults")
        return _cache["data"]


def cfg(key: str, default: Any = None) -> Any:
    """
    Get a config value. Priority: live_config.json → DEFAULTS → default arg.
    Called hot — cached, no disk IO unless TTL expired.
    """
    val = _load().get(key)
    if val is not None:
        return val
    return DEFAULTS.get(key, default)


def cfg_all() -> dict:
    """Return merged config (defaults + live_config overrides)."""
    merged = dict(DEFAULTS)
    merged.update(_load())
    return merged


def cfg_set(key: str, value: Any) -> None:
    """
    Write a single key to live_config.json and invalidate cache.
    Thread-safe for single-process use (bot is single-process).
    """
    try:
        try:
            existing = json.loads(_CONFIG_PATH.read_text())
        except Exception:
            existing = {}
        existing[key] = value
        _CONFIG_PATH.write_text(json.dumps(existing, indent=2))
        _cache["ts"] = 0.0  # invalidate
    except Exception as e:
        logger.warning(f"cfg_set({key}) failed: {e}")


def cfg_update(updates: dict) -> None:
    """Write multiple keys atomically."""
    try:
        try:
            existing = json.loads(_CONFIG_PATH.read_text())
        except Exception:
            existing = {}
        existing.update(updates)
        _CONFIG_PATH.write_text(json.dumps(existing, indent=2))
        _cache["ts"] = 0.0
    except Exception as e:
        logger.warning(f"cfg_update failed: {e}")
