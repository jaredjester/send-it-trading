"""Tests for evaluation module."""
from pathlib import Path

import pytest

from evaluation.alpha_tracker import AlphaTracker
from evaluation.deployment_gate import DeploymentGate


def test_alpha_tracker_basic(tmp_path):
    """AlphaTracker records and returns signal quality."""
    db_path = str(tmp_path / "alpha_metrics.json")
    tracker = AlphaTracker(db_path=db_path)

    # Need 20+ observations for IC calculation
    for i in range(25):
        tracker.record_signal_performance(
            signal_name="volume_spike",
            signal_strength=0.5 + i * 0.02,
            forward_return_1d=0.01 + i * 0.001,
            forward_return_5d=0.03 + i * 0.002,
            benchmark_return_1d=0.005,
        )

    quality = tracker.get_signal_quality("volume_spike")
    assert "ic_1d" in quality
    assert "has_edge" in quality
    assert quality["ic_1d"] is not None


def test_alpha_tracker_no_data():
    """AlphaTracker returns NO_DATA for unknown signals."""
    tracker = AlphaTracker(db_path="/tmp/nonexistent_alpha_test.json")
    quality = tracker.get_signal_quality("unknown_signal")
    assert quality["confidence"] == "NO_DATA"
    assert quality["has_edge"] is False


def test_deployment_gate_instantiates(tmp_path):
    """DeploymentGate can be instantiated with writable paths."""
    log_path = str(tmp_path / "deployment_log.jsonl")
    # DeploymentGate creates StrategyBacktester which writes to evaluation/;
    # use tmp_path for backtest db by running from a dir where evaluation exists
    import os
    orig_cwd = os.getcwd()
    try:
        (tmp_path / "evaluation").mkdir(exist_ok=True)
        os.chdir(tmp_path)
        gate = DeploymentGate(log_path=log_path)
        assert gate.backtester is not None
        assert gate.alpha_tracker is not None
    finally:
        os.chdir(orig_cwd)
