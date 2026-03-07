"""
Adaptive Signal Engine — Contextual bandit with Bayesian weight updating.

This is the brain of the learning system. It:
1. Maintains Beta distributions for each signal's accuracy
2. Updates beliefs after each trade outcome (Bayesian updating)
3. Applies exponential decay so recent performance matters more
4. Detects market regimes and adjusts strategy parameters
5. Produces dynamically-weighted signal scores for trade decisions

Designed for Raspberry Pi: numpy only, no deep learning frameworks.
"""

import json
import os
import math
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger("adaptive.engine")

LEARNING_DB_FILE = os.path.join(os.path.dirname(__file__), "learning_db.json")
WEIGHT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "weight_history.json")


class AdaptiveEngine:
    """
    Contextual bandit engine that learns optimal signal weights
    from actual trade outcomes over time.

    Core idea: Each signal (RSI, MACD, sentiment, etc.) is modeled as
    a Bernoulli bandit with a Beta(alpha, beta) prior. When a trade
    closes profitably and a signal contributed to entry, alpha increases.
    When it loses, beta increases. The expected accuracy (alpha/(alpha+beta))
    naturally shifts toward the signal's true predictive power.

    Exponential decay ensures recent performance is weighted more heavily,
    allowing adaptation to changing market regimes.
    """

    def __init__(self, config=None, db_path=None, history_path=None):
        self.config = config or {}
        self.db_path = db_path or LEARNING_DB_FILE
        self.history_path = history_path or WEIGHT_HISTORY_FILE
        self.db = self._load_db()

        # Extract learning params
        lc = self.config.get("learning", {})
        self.decay = lc.get("decay_factor", 0.95)
        self.min_trades = lc.get("min_trades_for_learning", 5)
        self.confidence_threshold = lc.get("confidence_threshold", 0.55)
        self.exploration_rate = lc.get("exploration_rate", 0.10)
        self.weight_floor = lc.get("weight_floor", 0.05)
        self.weight_ceiling = lc.get("weight_ceiling", 0.40)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_db(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load learning DB: {e}")

        # Initialize with priors from config
        db = {
            "signals": {},
            "sectors": {},
            "time_buckets": {},
            "regime": {
                "current": "unknown",
                "last_detected": None,
                "history": []
            },
            "global_stats": {
                "total_updates": 0,
                "total_wins": 0,
                "total_losses": 0,
                "last_update": None,
                "created": datetime.utcnow().isoformat() + "Z"
            },
            "version": 1
        }

        # Initialize signal priors
        sig_config = self.config.get("signals", {})
        for sig_name, params in sig_config.items():
            db["signals"][sig_name] = {
                "alpha": params.get("initial_alpha", 2),
                "beta": params.get("initial_beta", 2),
                "weight": params.get("initial_weight", 0.15),
                "ema_accuracy": 0.5,  # Exponential moving average
                "total_observations": 0,
                "recent_wins": 0,
                "recent_losses": 0,
                "streak": 0,  # Positive = winning streak, negative = losing
            }

        # Initialize sector priors
        sec_config = self.config.get("sectors", {})
        for sector, params in sec_config.items():
            db["sectors"][sector] = {
                "alpha": 2,
                "beta": 2,
                "weight": params.get("initial_weight", 1.0),
                "ema_win_rate": 0.5,
                "total_trades": 0
            }

        # Initialize time bucket priors
        tb_config = self.config.get("time_buckets", {})
        for bucket, params in tb_config.items():
            db["time_buckets"][bucket] = {
                "alpha": 2,
                "beta": 2,
                "weight": params.get("initial_weight", 1.0),
                "ema_win_rate": 0.5,
                "total_trades": 0
            }

        return db

    def _save_db(self):
        try:
            tmp = self.db_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.db, f, indent=2, default=str)
            os.replace(tmp, self.db_path)
        except Exception as e:
            logger.error(f"Failed to save learning DB: {e}")

    def _save_weight_snapshot(self):
        """Append current weights to history file for analysis."""
        snapshot = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "signal_weights": {
                name: data["weight"]
                for name, data in self.db["signals"].items()
            },
            "regime": self.db["regime"]["current"],
            "global_win_rate": self._global_win_rate()
        }

        history = []
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r") as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append(snapshot)
        # Keep last 365 days of daily snapshots
        if len(history) > 365:
            history = history[-365:]

        try:
            with open(self.history_path, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save weight history: {e}")

    # ------------------------------------------------------------------
    # Bayesian updating
    # ------------------------------------------------------------------
    def update_from_trade(self, closed_trade):
        """
        Update all beliefs based on a closed trade outcome.

        This is the core learning step. For each signal that contributed
        to the trade entry:
        - If the trade was profitable → increase alpha (successes)
        - If the trade lost → increase beta (failures)
        - Apply exponential decay to prevent old data from dominating
        - Recompute weights from posterior distributions
        """
        if not closed_trade:
            return

        win = closed_trade.get("win", False)
        signal_outcomes = closed_trade.get("signal_outcomes", {})
        sector = closed_trade.get("sector", "other")
        time_bucket = closed_trade.get("time_bucket", "unknown")
        pnl_pct = abs(closed_trade.get("pnl_pct", 0))

        # Scale update magnitude by PnL size (bigger wins/losses = stronger signal)
        # Clamp to [0.5, 2.0] to prevent extreme updates
        magnitude = max(0.5, min(2.0, 1.0 + pnl_pct / 5.0))

        # --- Update signal beliefs ---
        for sig_name, outcome in signal_outcomes.items():
            if sig_name not in self.db["signals"]:
                self.db["signals"][sig_name] = {
                    "alpha": 2, "beta": 2, "weight": 0.15,
                    "ema_accuracy": 0.5, "total_observations": 0,
                    "recent_wins": 0, "recent_losses": 0, "streak": 0
                }

            sig = self.db["signals"][sig_name]
            strength = outcome.get("entry_strength", 0.5)

            # Bayesian update: weight by signal strength at entry
            update_amount = magnitude * strength

            if outcome.get("correct", False):
                sig["alpha"] += update_amount
                sig["recent_wins"] += 1
                sig["streak"] = max(1, sig["streak"] + 1)
            else:
                sig["beta"] += update_amount
                sig["recent_losses"] += 1
                sig["streak"] = min(-1, sig["streak"] - 1)

            sig["total_observations"] += 1

            # Exponential decay on alpha/beta to forget old data
            sig["alpha"] = max(1.0, sig["alpha"] * self.decay +
                               (1 - self.decay) * 2)
            sig["beta"] = max(1.0, sig["beta"] * self.decay +
                              (1 - self.decay) * 2)

            # Update EMA accuracy
            new_accuracy = sig["alpha"] / (sig["alpha"] + sig["beta"])
            ema_alpha = 0.15  # EMA smoothing factor
            sig["ema_accuracy"] = (
                ema_alpha * (1.0 if outcome.get("correct") else 0.0) +
                (1 - ema_alpha) * sig["ema_accuracy"]
            )

        # --- Update sector beliefs ---
        if sector not in self.db["sectors"]:
            self.db["sectors"][sector] = {
                "alpha": 2, "beta": 2, "weight": 1.0,
                "ema_win_rate": 0.5, "total_trades": 0
            }
        sec = self.db["sectors"][sector]
        if win:
            sec["alpha"] += magnitude * 0.5
        else:
            sec["beta"] += magnitude * 0.5
        sec["alpha"] = max(1.0, sec["alpha"] * self.decay + (1 - self.decay) * 2)
        sec["beta"] = max(1.0, sec["beta"] * self.decay + (1 - self.decay) * 2)
        sec["total_trades"] += 1
        sec["ema_win_rate"] = 0.15 * (1.0 if win else 0.0) + 0.85 * sec["ema_win_rate"]

        # --- Update time bucket beliefs ---
        if time_bucket not in self.db["time_buckets"]:
            self.db["time_buckets"][time_bucket] = {
                "alpha": 2, "beta": 2, "weight": 1.0,
                "ema_win_rate": 0.5, "total_trades": 0
            }
        tb = self.db["time_buckets"][time_bucket]
        if win:
            tb["alpha"] += magnitude * 0.5
        else:
            tb["beta"] += magnitude * 0.5
        tb["alpha"] = max(1.0, tb["alpha"] * self.decay + (1 - self.decay) * 2)
        tb["beta"] = max(1.0, tb["beta"] * self.decay + (1 - self.decay) * 2)
        tb["total_trades"] += 1
        tb["ema_win_rate"] = 0.15 * (1.0 if win else 0.0) + 0.85 * tb["ema_win_rate"]

        # --- Global stats ---
        gs = self.db["global_stats"]
        gs["total_updates"] = gs.get("total_updates", 0) + 1
        if win:
            gs["total_wins"] = gs.get("total_wins", 0) + 1
        else:
            gs["total_losses"] = gs.get("total_losses", 0) + 1
        gs["last_update"] = datetime.utcnow().isoformat() + "Z"

        # Recompute all weights
        self._recompute_weights()
        self._save_db()

        logger.info(
            f"Updated beliefs from trade: "
            f"{'WIN' if win else 'LOSS'} {closed_trade.get('symbol', '?')} "
            f"({pnl_pct:+.2f}%) | {len(signal_outcomes)} signals updated"
        )

    def update_from_batch(self, closed_trades):
        """Process multiple closed trades at once."""
        for trade in closed_trades:
            self.update_from_trade(trade)
        if closed_trades:
            self._save_weight_snapshot()

    # ------------------------------------------------------------------
    # Weight computation
    # ------------------------------------------------------------------
    def _recompute_weights(self):
        """
        Recompute signal weights from Beta posteriors using Thompson Sampling.

        Each signal's weight is proportional to its expected accuracy
        (alpha / (alpha + beta)), clamped to [floor, ceiling].
        Weights are normalized to sum to 1.0.
        """
        signals = self.db["signals"]
        if not signals:
            return

        # Compute raw weights from posterior means
        raw_weights = {}
        for name, data in signals.items():
            # Expected value of Beta distribution
            expected = data["alpha"] / (data["alpha"] + data["beta"])

            # Blend with EMA for stability
            blended = 0.7 * expected + 0.3 * data.get("ema_accuracy", 0.5)

            # Apply confidence scaling: more observations → more extreme weights
            obs = data.get("total_observations", 0)
            confidence = min(1.0, obs / max(self.min_trades, 1))

            # Interpolate between uniform weight and learned weight
            uniform = 1.0 / len(signals)
            raw = confidence * blended + (1 - confidence) * uniform

            # Clamp to floor/ceiling
            raw_weights[name] = max(self.weight_floor,
                                    min(self.weight_ceiling, raw))

        # Normalize to sum to 1.0
        total = sum(raw_weights.values())
        if total > 0:
            for name in raw_weights:
                signals[name]["weight"] = round(raw_weights[name] / total, 6)

        # Update sector weights
        for sector, data in self.db["sectors"].items():
            expected = data["alpha"] / (data["alpha"] + data["beta"])
            data["weight"] = round(max(0.3, min(1.5, expected * 2)), 4)

        # Update time bucket weights
        for bucket, data in self.db["time_buckets"].items():
            expected = data["alpha"] / (data["alpha"] + data["beta"])
            data["weight"] = round(max(0.5, min(1.5, expected * 2)), 4)

    def _global_win_rate(self):
        gs = self.db["global_stats"]
        total = gs.get("total_wins", 0) + gs.get("total_losses", 0)
        return gs.get("total_wins", 0) / total if total > 0 else 0.5

    # ------------------------------------------------------------------
    # Signal scoring (the output other modules consume)
    # ------------------------------------------------------------------
    def score_opportunity(self, signals, sector="other", time_bucket=None):
        """
        Score a trading opportunity using dynamically learned weights.

        signals: dict of current signal readings, e.g.:
            {
                "rsi": {"value": 35, "signal": "buy", "strength": 0.7},
                "macd": {"value": 0.5, "signal": "buy", "strength": 0.6},
                ...
            }

        Returns: {
            "score": float (0-1, higher = stronger buy signal),
            "confidence": float (0-1, how reliable the score is),
            "signal_contributions": dict of per-signal weighted scores,
            "regime_adjustment": float,
            "sector_multiplier": float,
            "time_multiplier": float,
            "recommendation": "strong_buy" | "buy" | "hold" | "avoid"
        }
        """
        if not signals:
            return {"score": 0, "confidence": 0, "recommendation": "hold"}

        db_signals = self.db.get("signals", {})
        weighted_sum = 0
        total_weight = 0
        contributions = {}

        for sig_name, sig_data in signals.items():
            if not isinstance(sig_data, dict):
                continue

            strength = sig_data.get("strength", 0.5)
            signal_dir = sig_data.get("signal", "neutral")

            # Get learned weight (or use uniform)
            if sig_name in db_signals:
                weight = db_signals[sig_name]["weight"]
            else:
                weight = 1.0 / max(len(signals), 1)

            # Direction: buy signals are positive, sell/neutral are 0
            if signal_dir in ("buy", "bullish", "long"):
                dir_score = strength
            elif signal_dir in ("sell", "bearish", "short"):
                dir_score = -strength
            else:
                dir_score = 0

            weighted_score = weight * dir_score
            weighted_sum += weighted_score
            total_weight += weight

            contributions[sig_name] = {
                "weight": round(weight, 4),
                "strength": round(strength, 4),
                "direction": signal_dir,
                "weighted_score": round(weighted_score, 4)
            }

        # Normalize score to 0-1 range
        if total_weight > 0:
            raw_score = (weighted_sum / total_weight + 1) / 2  # Map [-1,1] to [0,1]
        else:
            raw_score = 0.5

        # Sector multiplier
        sec_data = self.db.get("sectors", {}).get(sector, {})
        sector_mult = sec_data.get("weight", 1.0)

        # Time bucket multiplier
        tb_data = self.db.get("time_buckets", {}).get(time_bucket, {})
        time_mult = tb_data.get("weight", 1.0)

        # Regime adjustment
        regime = self.db.get("regime", {}).get("current", "unknown")
        regime_adj = self._regime_score_adjustment(regime)

        # Final score
        final_score = raw_score * sector_mult * time_mult * regime_adj
        final_score = max(0, min(1, final_score))

        # Confidence based on data quantity
        total_obs = sum(
            db_signals.get(s, {}).get("total_observations", 0)
            for s in signals
        )
        confidence = min(1.0, total_obs / (self.min_trades * len(signals)))

        # Exploration: occasionally boost score for uncertain signals
        if np and confidence < 0.5 and np.random.random() < self.exploration_rate:
            final_score = min(1.0, final_score + 0.1)
            logger.debug("Exploration boost applied")

        # Recommendation
        if final_score >= 0.75 and confidence >= 0.5:
            rec = "strong_buy"
        elif final_score >= 0.60:
            rec = "buy"
        elif final_score <= 0.30:
            rec = "avoid"
        else:
            rec = "hold"

        return {
            "score": round(final_score, 4),
            "confidence": round(confidence, 4),
            "signal_contributions": contributions,
            "sector_multiplier": round(sector_mult, 4),
            "time_multiplier": round(time_mult, 4),
            "regime": regime,
            "regime_adjustment": round(regime_adj, 4),
            "recommendation": rec
        }

    def _regime_score_adjustment(self, regime):
        """Adjust scoring based on detected market regime."""
        adjustments = {
            "trending_up": 1.1,     # Slightly more aggressive in uptrends
            "trending_down": 0.7,   # Much more cautious in downtrends
            "high_volatility": 0.8, # Cautious in volatile markets
            "low_volatility": 1.0,  # Neutral in calm markets
            "mean_reverting": 0.9,  # Slightly cautious
            "unknown": 1.0
        }
        return adjustments.get(regime, 1.0)

    # ------------------------------------------------------------------
    # Regime detection
    # ------------------------------------------------------------------
    def detect_regime(self, price_data):
        """
        Detect current market regime from recent price data.

        price_data: list of dicts with at least {"close": float, "high": float,
                    "low": float, "volume": float}

        Updates self.db["regime"] with detected regime.
        """
        if not np or not price_data or len(price_data) < 20:
            return "unknown"

        closes = np.array([d["close"] for d in price_data[-50:]])

        # Volatility (annualized from daily returns)
        if len(closes) >= 2:
            returns = np.diff(closes) / closes[:-1]
            vol = np.std(returns)
        else:
            vol = 0

        # Trend (simple: are we above or below 20-day SMA?)
        sma20 = np.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
        current = closes[-1]
        trend_pct = (current - sma20) / sma20 if sma20 > 0 else 0

        # ADX-like trend strength (simplified)
        if len(closes) >= 14:
            diffs = np.abs(np.diff(closes[-15:]))
            avg_move = np.mean(diffs)
            net_move = abs(closes[-1] - closes[-15])
            trend_strength = net_move / (avg_move * 14) if avg_move > 0 else 0
        else:
            trend_strength = 0

        # Classify regime
        rc = self.config.get("regime", {})
        high_vol = rc.get("high_vol_threshold", 0.025)

        if vol > high_vol:
            regime = "high_volatility"
        elif trend_pct > 0.03 and trend_strength > 0.3:
            regime = "trending_up"
        elif trend_pct < -0.03 and trend_strength > 0.3:
            regime = "trending_down"
        elif trend_strength < 0.15:
            regime = "mean_reverting"
        else:
            regime = "low_volatility"

        # Update DB
        old_regime = self.db["regime"]["current"]
        self.db["regime"]["current"] = regime
        self.db["regime"]["last_detected"] = datetime.utcnow().isoformat() + "Z"

        if regime != old_regime:
            self.db["regime"]["history"].append({
                "regime": regime,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "volatility": round(float(vol), 6),
                "trend_pct": round(float(trend_pct), 4)
            })
            # Keep last 100 regime changes
            self.db["regime"]["history"] = self.db["regime"]["history"][-100:]
            logger.info(f"Regime change: {old_regime} → {regime}")

        self._save_db()
        return regime

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def get_current_weights(self):
        """Return current signal weights as a clean dict."""
        return {
            name: {
                "weight": round(data["weight"], 4),
                "accuracy": round(
                    data["alpha"] / (data["alpha"] + data["beta"]), 4
                ),
                "ema_accuracy": round(data.get("ema_accuracy", 0.5), 4),
                "observations": data.get("total_observations", 0),
                "streak": data.get("streak", 0)
            }
            for name, data in self.db.get("signals", {}).items()
        }

    def get_learning_summary(self):
        """Full summary of what the engine has learned."""
        weights = self.get_current_weights()
        gs = self.db.get("global_stats", {})

        # Best and worst signals
        sorted_sigs = sorted(
            weights.items(),
            key=lambda x: x[1]["accuracy"],
            reverse=True
        )

        return {
            "total_updates": gs.get("total_updates", 0),
            "global_win_rate": round(self._global_win_rate(), 4),
            "regime": self.db.get("regime", {}).get("current", "unknown"),
            "signal_weights": weights,
            "best_signal": sorted_sigs[0] if sorted_sigs else None,
            "worst_signal": sorted_sigs[-1] if sorted_sigs else None,
            "sector_performance": {
                name: {
                    "weight": data["weight"],
                    "win_rate": round(
                        data["alpha"] / (data["alpha"] + data["beta"]), 4
                    ),
                    "trades": data.get("total_trades", 0)
                }
                for name, data in self.db.get("sectors", {}).items()
            },
            "time_performance": {
                name: {
                    "weight": data["weight"],
                    "win_rate": round(
                        data["alpha"] / (data["alpha"] + data["beta"]), 4
                    ),
                    "trades": data.get("total_trades", 0)
                }
                for name, data in self.db.get("time_buckets", {}).items()
            },
            "last_update": gs.get("last_update"),
            "data_sufficiency": (
                "learning" if gs.get("total_updates", 0) >= self.min_trades
                else f"collecting ({gs.get('total_updates', 0)}/{self.min_trades})"
            )
        }

    def report(self):
        """Human-readable report string."""
        s = self.get_learning_summary()
        lines = [
            "═══ Adaptive Engine Report ═══",
            f"Updates: {s['total_updates']} | "
            f"Win Rate: {s['global_win_rate']:.1%} | "
            f"Regime: {s['regime']}",
            f"Status: {s['data_sufficiency']}",
            "",
            "Signal Weights (learned):"
        ]

        for name, data in sorted(
            s["signal_weights"].items(),
            key=lambda x: x[1]["weight"],
            reverse=True
        ):
            bar_len = int(data["weight"] * 40)
            bar = "█" * bar_len + "░" * (8 - bar_len)
            streak_s = (
                f"+{data['streak']}" if data["streak"] > 0
                else str(data["streak"]) if data["streak"] < 0
                else "0"
            )
            lines.append(
                f"  {name:12s} {bar} "
                f"w={data['weight']:.3f} "
                f"acc={data['accuracy']:.1%} "
                f"n={data['observations']} "
                f"streak={streak_s}"
            )

        if s.get("best_signal"):
            bname, bdata = s["best_signal"]
            lines.append(f"\n★ Best: {bname} ({bdata['accuracy']:.1%} accuracy)")
        if s.get("worst_signal"):
            wname, wdata = s["worst_signal"]
            lines.append(f"✗ Worst: {wname} ({wdata['accuracy']:.1%} accuracy)")

        return "\n".join(lines)
