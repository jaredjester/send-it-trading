"""Tests for signal + Kelly sizing."""
import pytest

from core.sizing import EdgeEstimate, KellyConfig, kelly_fraction, size_position, synthesize_edge, unified_position_size


def test_kelly_formula():
    """Kelly fraction for p=0.6, B=1 (even money) => f*=0.2."""
    f = kelly_fraction(p=0.6, B=1.0)
    assert abs(f - 0.2) < 0.001


def test_kelly_no_edge():
    """Negative edge => f*=0."""
    assert kelly_fraction(p=0.45, B=1.0) == 0.0
    assert kelly_fraction(p=0.5, B=0.5) == 0.0


def test_synthesize_edge_from_alpha():
    """Alpha output → EdgeEstimate with reasonable p, B."""
    alpha = {
        "score": 72,
        "confidence": 0.72,
        "strategy": "mean_reversion",
        "suggested_action": "strong_buy",
        "entry_price": 100,
        "stop_loss": 92,
        "take_profit": 115,
        "signals": {
            "mean_reversion": {
                "rsi": 28, "is_oversold": True, "is_below_mean": True,
                "has_volume_spike": True, "volume_ratio": 2.0,
            },
        },
    }
    edge = synthesize_edge(alpha, regime="neutral")
    assert edge.p > 0.5
    assert edge.B > 0
    assert edge.confluence > 0
    assert edge.strategy == "mean_reversion"


def test_size_position():
    """Position size from alpha → dollars."""
    alpha = {
        "score": 70,
        "confidence": 0.7,
        "strategy": "momentum",
        "suggested_action": "buy",
        "entry_price": 50,
        "stop_loss": 46,
        "take_profit": 58,
        "signals": {},
    }
    cfg = KellyConfig(fractional=0.5, max_position_pct=0.20)
    result = size_position(alpha, portfolio_value=1000, config=cfg)
    assert "position_size" in result
    assert "approved" in result
    assert result["position_size"] >= 0
    assert result["position_size"] <= 200  # 20% of 1000


def test_unified_position_size():
    """Full flow: alpha → edge → Kelly → position."""
    alpha = {
        "score": 68,
        "confidence": 0.68,
        "strategy": "mean_reversion",
        "suggested_action": "buy",
        "entry_price": 100,
        "stop_loss": 94,
        "take_profit": 110,
        "signals": {"mean_reversion": {"is_oversold": True, "has_volume_spike": True}},
    }
    result = unified_position_size(
        alpha, portfolio_value=500, regime="bull", current_positions=2
    )
    assert "position_size" in result
    assert "fraction" in result
    assert "approved" in result
    assert "edge" in result
