"""
DEPRECATED: Legacy config loader.
All configuration now flows through dynamic_config.cfg() → live_config.json.
This file is kept for backwards compatibility only.
"""
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)

_warned = False


def get_project_root() -> Path:
    """Return the engine directory (project root for engine modules)."""
    return Path(__file__).resolve().parent.parent


def load_config(config_path=None) -> dict:
    """
    DEPRECATED — returns empty config dict.
    Use `from core.dynamic_config import cfg` instead.
    """
    global _warned
    if not _warned:
        logger.warning(
            "load_config() is deprecated. Use dynamic_config.cfg() for all configuration. "
            "Returning empty dict."
        )
        _warned = True
    return {}


# Backwards compat alias
get_config = load_config
