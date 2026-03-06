"""
Position sizing: signal + pattern + Kelly in one flow.

Alpha → edge (p, B) → Kelly f* → fractional + caps → dollars.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.config import load_config

logger = logging.getLogger(__name__)


@dataclass
class EdgeEstimate:
    """Win prob p, payoff B, confluence."""

    p: float
    B: float
    confluence: float
    strategy: str
    confidence: float
    rationale: list[str] = field(default_factory=list)


@dataclass
class KellyConfig:
    fractional: float = 0.5
    max_position_pct: float = 0.20
    min_position_pct: float = 0.01
    shrink_on_low_confluence: float = 0.7
    max_kelly_fraction: float = 0.25
    min_edge_to_bet: float = 0.01


def _extract_signals(alpha: dict) -> dict:
    signals = alpha.get("signals", {}) or {}
    if "mean_reversion" in signals:
        return {
            "mean_reversion": signals.get("mean_reversion", {}),
            "momentum": signals.get("momentum", {}),
            "sentiment": signals.get("sentiment", {}),
        }
    r, v, t, a = signals.get("rsi", {}), signals.get("volume", {}), signals.get("trend", {}), signals.get("adx", {})
    return {
        "mean_reversion": {
            "rsi": r.get("value", 50),
            "is_oversold": r.get("value", 50) < 30,
            "volume_ratio": v.get("ratio", 1.0),
            "has_volume_spike": v.get("signal") == "surge" or v.get("ratio", 0) > 1.5,
            "is_below_mean": r.get("signal") in ("oversold", "approaching_oversold"),
        },
        "momentum": {
            "adx": a.get("value", 0),
            "has_strong_trend": a.get("value", 0) > 25 or a.get("trending", False),
            "is_trending_up": t.get("signal") in ("aligned_up", "above_sma20"),
            "has_volume_growth": v.get("ratio", 1.0) > 1.2,
        },
        "sentiment": {},
    }


def _confluence(alpha: dict) -> float:
    raw = _extract_signals(alpha)
    mr, mom, sent = raw.get("mean_reversion", {}), raw.get("momentum", {}), raw.get("sentiment", {})
    scores = []
    if mr:
        n = sum([mr.get("is_oversold", False), mr.get("is_below_mean", False), mr.get("has_volume_spike", False)])
        scores.append((n / 3) if alpha.get("strategy") == "mean_reversion" else 0.5 * n / 3)
    if mom:
        n = sum([mom.get("is_trending_up", False), mom.get("has_strong_trend", False), mom.get("has_volume_growth", False)])
        scores.append((n / 3) if alpha.get("strategy") == "momentum" else 0.5 * n / 3)
    if sent and (sent.get("positive_sentiment") or sent.get("score", 0) > 0):
        scores.append(1.0 if alpha.get("strategy") == "sentiment_enhanced" else 0.5)
    return min(1.0, sum(scores) / len(scores) + 0.1) if scores else float(alpha.get("confidence", 0.5))


def _payoff_B(alpha: dict) -> float:
    e = float(alpha.get("entry_price") or alpha.get("current_price") or 1)
    s, t = float(alpha.get("stop_loss", 0)), float(alpha.get("take_profit", 0))
    risk = e - s if e > s else e * 0.05
    reward = t - e if t > e else e * 0.05
    return max(0.25, min(4.0, reward / risk))


def _score_to_p(score: float) -> float:
    return max(0.48, min(0.70, 0.50 + (score - 50) * 0.003))


_REGIME = {("bull", "momentum"): 1.0, ("bull", "mean_reversion"): 0.85, ("bear", "momentum"): 0.7, ("bear", "mean_reversion"): 1.0}


def synthesize_edge(alpha: dict, regime: str = "unknown", hit_rate: float | None = None, ic: float | None = None) -> EdgeEstimate:
    action = alpha.get("suggested_action", alpha.get("action", "hold"))
    if action in ("sell", "strong_sell", "hold", "skip"):
        return EdgeEstimate(0.50, 1.0, 0.0, "", 0.0, ["No edge"])
    confluence = _confluence(alpha)
    B = _payoff_B(alpha)
    p = _score_to_p(float(alpha.get("score", 50)))
    if hit_rate is not None:
        p = 0.6 * p + 0.4 * hit_rate
    if ic is not None and ic > 0:
        p = min(0.68, p + min(0.05, ic * 0.3))
    mod = _REGIME.get((regime, alpha.get("strategy", "")), 0.95) if regime != "unknown" else 1.0
    p = min(0.68, p * mod * (0.7 + 0.3 * confluence))
    return EdgeEstimate(p, B, confluence, str(alpha.get("strategy", "")), float(alpha.get("confidence", 0.5)), [])


def kelly_fraction(p: float, B: float) -> float:
    ev = B * p - (1 - p)
    return ev / B if ev > 0 and B > 0 else 0.0


def size_position(
    alpha: dict,
    portfolio_value: float,
    config: KellyConfig | None = None,
    regime: str = "unknown",
    hit_rate: float | None = None,
    ic: float | None = None,
    active_positions: int = 0,
) -> dict[str, Any]:
    cfg = config or KellyConfig()
    kc = load_config().get("kelly_sizing", {})
    cfg = KellyConfig(
        fractional=kc.get("fractional", cfg.fractional),
        max_position_pct=kc.get("max_position_pct", cfg.max_position_pct),
        min_position_pct=kc.get("min_position_pct", cfg.min_position_pct),
        shrink_on_low_confluence=kc.get("shrink_on_low_confluence", cfg.shrink_on_low_confluence),
        max_kelly_fraction=kc.get("max_kelly_fraction", cfg.max_kelly_fraction),
        min_edge_to_bet=kc.get("min_edge_to_bet", cfg.min_edge_to_bet),
    )
    edge = synthesize_edge(alpha, regime, hit_rate, ic)
    if edge.p <= 0.5 or edge.B <= 0:
        return {"position_size": 0.0, "fraction": 0.0, "approved": False, "edge": edge, "rationale": ["No edge"]}
    f = cfg.fractional * kelly_fraction(edge.p, edge.B)
    if f <= cfg.min_edge_to_bet:
        return {"position_size": 0.0, "fraction": 0.0, "approved": False, "edge": edge, "rationale": ["Kelly below min"]}
    if edge.confluence < 0.5:
        f *= cfg.shrink_on_low_confluence
    f = min(f, cfg.max_kelly_fraction, cfg.max_position_pct)
    f = max(f, cfg.min_position_pct) if f > 0 else 0
    if active_positions > 0:
        f *= 1.0 / (1.0 + 0.1 * active_positions)
    return {"position_size": round(portfolio_value * f, 2), "fraction": f, "approved": portfolio_value * f >= 5, "edge": edge, "rationale": []}


def unified_position_size(
    alpha_output: dict,
    portfolio_value: float,
    regime: str = "unknown",
    alpha_tracker_signal: str | None = None,
    alpha_tracker=None,
    circuit_breaker_status: dict | None = None,
    current_positions: int = 0,
) -> dict[str, Any]:
    if circuit_breaker_status and not circuit_breaker_status.get("all_clear", True):
        if "halt_new_buys" in circuit_breaker_status.get("restrictions", []):
            return {"position_size": 0.0, "fraction": 0.0, "approved": False, "edge": None, "rationale": ["Halt"], "adjustments": []}
    hit_rate, ic = None, None
    if alpha_tracker and alpha_tracker_signal:
        try:
            q = alpha_tracker.get_signal_quality(alpha_tracker_signal)
            hit_rate, ic = q.get("hit_rate"), q.get("ic_1d") or q.get("recent_ic")
        except Exception:
            pass
    r = size_position(alpha_output, portfolio_value, regime=regime, hit_rate=hit_rate, ic=ic, active_positions=current_positions)
    r["adjustments"] = r.get("rationale", [])
    return r
