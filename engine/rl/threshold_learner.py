#!/usr/bin/env python3
"""
Threshold Learner — RL-trained score threshold via Thompson Sampling bandit.

The orchestrator filters trade candidates by a minimum alpha score threshold.
Instead of hardcoding this value, we learn the optimal threshold from trade outcomes.

Algorithm: Thompson Sampling (Bayesian bandit)
  - Maintains Beta(alpha, beta) distribution per (regime, threshold_bucket)
  - At market open: samples each bucket, picks highest → sets min_score_threshold
  - At trade close: updates winner/loser counts for that day's active threshold
  - Regime-aware: bull/bear/neutral/unknown each get independent distributions

Threshold buckets explored: [25, 30, 35, 40, 45, 50, 55, 60, 65, 70]
State file: evaluation/threshold_bandit.json

Reward shaping:
  - Win  (+pnl): alpha += 1 + min(pnl_pct * 10, 2.0)   → +1 to +3
  - Loss (-pnl): beta  += 1 + min(|pnl_pct| * 10, 2.0) → +1 to +3
  - Magnitude weighting means big wins/losses teach faster than small ones

Exploration:
  - ε decays from 0.30 → 0.05 as trades accumulate per regime
  - With little data the bandit explores widely; with data it exploits

Integration:
  Called by EpisodeBridge:
    on_market_open() → select_threshold(regime) → writes to live_config.json
    on_trade_closed() → record_outcome(pnl_pct)
"""

import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rl.threshold_learner")

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.dynamic_config import cfg as _cfg

REGIMES = ["bull", "bear", "neutral", "unknown"]

# Buckets and default loaded from cfg so they can be tuned
def _buckets() -> list:
    return _cfg("rl_threshold_buckets", [25, 30, 35, 40, 45, 50, 55, 60, 65, 70])

def _default_threshold() -> int:
    return int(_cfg("rl_default_threshold", 45))

STATE_FILE = Path(__file__).parent.parent / "evaluation" / "threshold_bandit.json"


class ThresholdLearner:
    """
    Thompson Sampling Bayesian bandit that learns the optimal score threshold
    for each market regime.

    Usage:
        learner = ThresholdLearner()

        # Market open — pick today's threshold
        threshold = learner.select_threshold(regime="neutral")
        # → writes to live_config.json automatically

        # Trade closed — teach the bandit
        learner.record_outcome(pnl_pct=0.05)   # win
        learner.record_outcome(pnl_pct=-0.03)  # loss
    """

    def __init__(self):
        # {regime: {str(threshold): {"alpha": float, "beta": float, "trades": int, "total_pnl": float}}}
        self.state: dict = {}
        self._active_threshold: Optional[int] = None
        self._active_regime: str = "unknown"
        self._session_outcomes: list = []  # [(pnl_pct, threshold, regime)]
        self._load()

    # ──────────────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def _load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    self.state = json.load(f)
                total_pairs = sum(len(v) for v in self.state.values())
                total_trades = sum(
                    e.get("trades", 0)
                    for regime_data in self.state.values()
                    for e in regime_data.values()
                )
                logger.info(
                    f"ThresholdLearner loaded: {total_pairs} (regime, threshold) pairs, "
                    f"{total_trades} total trades"
                )
            except Exception as e:
                logger.warning(f"ThresholdLearner load failed: {e}")
                self.state = {}

    def _save(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(STATE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            logger.warning(f"ThresholdLearner save failed: {e}")

    def _get_entry(self, regime: str, threshold: int) -> dict:
        """Get or create Beta distribution entry for (regime, threshold)."""
        if regime not in self.state:
            self.state[regime] = {}
        key = str(threshold)
        if key not in self.state[regime]:
            self.state[regime][key] = {
                "alpha": 1.0,   # prior: 1 pseudo-win
                "beta": 1.0,    # prior: 1 pseudo-loss
                "trades": 0,
                "total_pnl": 0.0,
                "last_selected": None,
            }
        return self.state[regime][key]

    # ──────────────────────────────────────────────────────────────────────────
    # Core API
    # ──────────────────────────────────────────────────────────────────────────

    def select_threshold(self, regime: str = "unknown") -> int:
        """
        Thompson Sampling: sample Beta(alpha, beta) for each bucket,
        return the threshold with the highest sample.

        Also writes the chosen threshold to live_config.json so the
        orchestrator picks it up within 60s.

        Returns:
            int: selected threshold (e.g. 35, 45, 60)
        """
        self._active_regime = regime

        # Count total trades for this regime (for epsilon decay)
        total_regime_trades = sum(
            self._get_entry(regime, t)["trades"]
            for t in _buckets()
        )

        # Epsilon decays from 0.30 → 0.05 as we accumulate regime data
        epsilon = max(0.05, 0.30 - 0.005 * total_regime_trades)

        # ε-greedy: sometimes explore a random bucket
        if random.random() < epsilon:
            chosen = random.choice(_buckets())
            logger.info(
                f"ThresholdLearner [{regime}]: EXPLORE → {chosen} "
                f"(ε={epsilon:.2f}, regime_trades={total_regime_trades})"
            )
        else:
            # Thompson sampling: draw from each Beta, pick max
            samples = {}
            for t in _buckets():
                entry = self._get_entry(regime, t)
                a, b = entry["alpha"], entry["beta"]
                try:
                    import numpy as np
                    sample = float(np.random.beta(a, b))
                except ImportError:
                    # Pure Python fallback: approximate Beta via mean + noise
                    mean = a / (a + b)
                    # Variance proxy: tighter as alpha+beta grows
                    spread = 0.15 / max(1.0, (a + b) ** 0.5)
                    sample = max(0.0, min(1.0, random.gauss(mean, spread)))
                samples[t] = sample

            chosen = max(samples, key=samples.get)
            best_sample = samples[chosen]
            logger.info(
                f"ThresholdLearner [{regime}]: EXPLOIT → {chosen} "
                f"(sample={best_sample:.3f}, ε={epsilon:.2f})"
            )

        # Mark selection time
        entry = self._get_entry(regime, chosen)
        entry["last_selected"] = datetime.now().isoformat()
        self._active_threshold = chosen
        self._save()

        # Write to live_config.json so orchestrator picks it up
        self._write_to_config(chosen, regime)

        return chosen

    def record_outcome(
        self,
        pnl_pct: float,
        threshold: Optional[int] = None,
        regime: Optional[str] = None,
    ):
        """
        Update the Beta distribution for the active (regime, threshold) pair.

        Args:
            pnl_pct: realized return, e.g. 0.05 = +5%, -0.03 = -3%
            threshold: override active threshold (optional)
            regime: override active regime (optional)
        """
        t = threshold if threshold is not None else self._active_threshold
        r = regime or self._active_regime

        if t is None:
            logger.debug("ThresholdLearner: no active threshold to record against")
            return

        entry = self._get_entry(r, t)
        entry["trades"] += 1
        entry["total_pnl"] = round(entry.get("total_pnl", 0.0) + pnl_pct, 4)

        if pnl_pct > 0:
            # Win — boost alpha proportionally (big wins teach more)
            boost = 1.0 + min(pnl_pct * 10.0, 2.0)   # +1.0 to +3.0
            entry["alpha"] = round(entry["alpha"] + boost, 3)
            logger.info(
                f"ThresholdLearner [{r}/{t}]: WIN  pnl={pnl_pct:+.2%} → "
                f"alpha+={boost:.2f} (now {entry['alpha']:.2f})"
            )
        else:
            # Loss — boost beta proportionally (big losses teach more)
            boost = 1.0 + min(abs(pnl_pct) * 10.0, 2.0)  # +1.0 to +3.0
            entry["beta"] = round(entry["beta"] + boost, 3)
            logger.info(
                f"ThresholdLearner [{r}/{t}]: LOSS pnl={pnl_pct:+.2%} → "
                f"beta+={boost:.2f} (now {entry['beta']:.2f})"
            )

        self._session_outcomes.append((pnl_pct, t, r))
        self._save()

    def get_best_threshold(self, regime: str = "unknown") -> int:
        """
        Return the threshold with the highest estimated win rate (Beta mean)
        for the given regime, using only buckets with at least 3 real trades.
        Falls back to _default_threshold() if insufficient data.
        """
        best_t = _default_threshold()
        best_rate = -1.0

        for t in _buckets():
            entry = self._get_entry(regime, t)
            if entry["trades"] >= 3:
                a, b = entry["alpha"], entry["beta"]
                rate = a / (a + b)
                if rate > best_rate:
                    best_rate = rate
                    best_t = t

        if best_rate < 0:
            logger.debug(
                f"ThresholdLearner: not enough data for [{regime}], "
                f"using default {_default_threshold()}"
            )
        else:
            logger.debug(
                f"ThresholdLearner: best [{regime}] threshold={best_t} "
                f"win_rate={best_rate:.1%}"
            )

        return best_t

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _write_to_config(self, threshold: int, regime: str):
        """Persist RL-selected threshold to live_config.json."""
        try:
            import sys
            _STRATEGY_DIR = Path(__file__).parent.parent
            sys.path.insert(0, str(_STRATEGY_DIR))
            from core.dynamic_config import cfg_update
            cfg_update({
                "min_score_threshold": threshold,
                "rl_threshold_regime": regime,
                "rl_threshold_updated_at": datetime.now().isoformat(),
            })
            logger.info(
                f"ThresholdLearner → live_config: threshold={threshold} regime={regime}"
            )
        except Exception as e:
            logger.warning(f"ThresholdLearner config write failed: {e}")

    def summary(self, regime: str = "unknown") -> str:
        """Human-readable summary of learned thresholds for a regime."""
        lines = [f"\n📊 Threshold Bandit [{regime}] (higher bar = better win rate):"]
        lines.append(f"  {'Thresh':>6} | {'Win Rate':>8} | {'Trades':>6} | {'Total PnL':>9} | Confidence")
        lines.append(f"  {'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*9}-+-{'-'*20}")

        for t in _buckets():
            entry = self._get_entry(regime, t)
            a, b = entry["alpha"], entry["beta"]
            rate = a / (a + b)
            trades = entry["trades"]
            pnl = entry.get("total_pnl", 0.0)
            # Confidence: higher alpha+beta means tighter distribution
            confidence = min((a + b - 2) / 20.0, 1.0)  # 0-1
            bar = "█" * int(rate * 10) + "░" * (10 - int(rate * 10))
            active = " ◄" if t == self._active_threshold and regime == self._active_regime else ""
            lines.append(
                f"  {t:>6} | {rate:>7.1%} | {trades:>6} | {pnl:>+8.2%} | "
                f"[{bar}] {confidence:.0%}{active}"
            )

        lines.append(
            f"\n  Active: threshold={self._active_threshold}, regime={self._active_regime}"
        )
        return "\n".join(lines)

    def all_summaries(self) -> str:
        """Summary across all regimes that have data."""
        parts = []
        for regime in REGIMES:
            if regime in self.state and any(
                e.get("trades", 0) > 0
                for e in self.state[regime].values()
            ):
                parts.append(self.summary(regime))
        if not parts:
            return "ThresholdLearner: no trade data yet — exploring all thresholds"
        return "\n".join(parts)
