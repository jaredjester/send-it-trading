"""
Central config loading with env var overrides and path resolution.

- Credentials: env vars override config file
- Paths: resolved relative to project root or STRATEGY_ROOT
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def get_project_root() -> Path:
    """Project root, overridable via STRATEGY_ROOT."""
    root = os.getenv("STRATEGY_ROOT")
    return Path(root).expanduser().resolve() if root else PROJECT_ROOT


def resolve_path(path_str: str) -> Path:
    """Resolve path: absolute stays, relative becomes project-relative."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = get_project_root() / p
    return p.resolve()


def load_config(config_path: str | Path | None = None) -> dict:
    """
    Load master config. Credentials from env override config file.

    Config path order: explicit arg > master_config.json in project root > None.
    """
    root = get_project_root()
    if config_path is None:
        config_path = root / "master_config.json"
    else:
        config_path = Path(config_path)
    if not config_path.exists():
        return {}

    with open(config_path) as f:
        config = json.load(f)

    # Override credentials with env vars when present
    acct = config.setdefault("account", {})
    env_key = os.getenv("ALPACA_API_LIVE_KEY") or os.getenv("APCA_API_KEY_ID")
    env_secret = os.getenv("ALPACA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY")
    if env_key:
        acct["alpaca_api_key"] = env_key
    if env_secret:
        acct["alpaca_secret_key"] = env_secret

    # Resolve known path fields relative to project root
    if "execution_gate" in config and config["execution_gate"].get("rl_state_path"):
        config["execution_gate"]["rl_state_path"] = str(
            resolve_path(config["execution_gate"]["rl_state_path"])
        )
    if "benchmark" in config and config["benchmark"].get("state_file"):
        config["benchmark"]["state_file"] = str(
            resolve_path(config["benchmark"]["state_file"])
        )
    if "logging" in config and config["logging"].get("log_file"):
        config["logging"]["log_file"] = str(
            resolve_path(config["logging"]["log_file"])
        )

    return config
