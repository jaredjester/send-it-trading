"""Tests for core.config."""
import json
from pathlib import Path

import pytest

from core.config import get_project_root, load_config, resolve_path


def test_get_project_root():
    """Project root should contain core/config.py."""
    root = get_project_root()
    assert root.exists()
    assert (root / "core" / "config.py").exists()


def test_resolve_path_relative():
    """Relative paths resolve to project root."""
    res = resolve_path("./state/benchmark.json")
    assert res.is_absolute()
    assert "state" in str(res)
    assert "benchmark.json" in str(res)


def test_resolve_path_absolute():
    """Absolute paths pass through."""
    abs_path = Path("/tmp/foo.json")
    res = resolve_path(str(abs_path))
    assert res == abs_path.resolve()


def test_load_config_empty_when_missing(no_real_credentials):
    """load_config returns {} when master_config.json does not exist."""
    # Use a path that won't exist
    config = load_config(Path("/nonexistent/path/master_config.json"))
    assert config == {}


def test_load_config_with_mock(mock_config, no_real_credentials):
    """load_config loads from provided path."""
    config = load_config(mock_config)
    assert config
    assert config["account"]["alpaca_api_key"] == "test_key"
    assert config["account"]["alpaca_secret_key"] == "test_secret"
