#!/usr/bin/env python3
"""
Episode Bridge — wires orchestrator trade events into the adaptive RL system.

The existing adaptive/ system (EpisodeManager + QLearner) was built but never
connected to the orchestrator. This bridge is the missing wire.

Episode lifecycle (one trading day = one episode):
  9:30 AM  → on_market_open()       start episode, record initial state,
                                    ThresholdLearner selects today's min_score_threshold
  Each buy → on_trade()             record (state, action, score, notional)
  Each sell/zombie → on_trade_closed()  record reward, teach ThresholdLearner
  4:00 PM  → on_market_close()      end episode, run MC Q-update,
                                    write rl_action + multipliers to live_config

Reward signal:
  reward = pnl_pct * 100  (e.g. +5% return → reward +5.0)
  Penalize excessive trades: -0.2 per trade beyond 2 in a session
  Bonus for beating SPY: +1.0 if episode_return > spy_return (not yet impl)

Q-action → orchestrator behavior mapping:
  aggressive_buy → trade_mult=1.5, size_mult=1.3  (push harder)
  moderate_buy   → trade_mult=1.0, size_mult=1.0  (normal)
  hold           → trade_mult=0.5, size_mult=0.8  (cautious)
  reduce         → trade_mult=0.0, size_mult=0.0  (no new buys)
  defensive      → trade_mult=0.0, size_mult=0.0  (close risk)

Threshold learning (ThresholdLearner):
  - Thompson Sampling bandit across threshold buckets [25..70]
  - Regime-aware: learns separate distributions for bull/bear/neutral/unknown
  - Selects threshold at market open, updates on every trade close
  - Writes min_score_threshold to live_config.json (cfg() picks it up in <60s)
"""

import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("rl.episode_bridge")

# Point to adaptive/ directory so we can import its modules
_ADAPTIVE_DIR = Path(__file__).parent.parent / "adaptive"  # engine/adaptive/
_STRATEGY_DIR = Path(__file__).parent.parent

# Multipliers applied to config params based on Q-recommended action
_ACTION_MULTIPLIERS = {
    "aggressive_buy": {"trade_mult": 1.5, "size_mult": 1.3},
    "moderate_buy":   {"trade_mult": 1.0, "size_mult": 1.0},
    "hold":           {"trade_mult": 0.5, "size_mult": 0.8},
    "reduce":         {"trade_mult": 0.0, "size_mult": 0.0},
    "defensive":      {"trade_mult": 0.0, "size_mult": 0.0},
}


class EpisodeBridge:
    """
    Connects orchestrator events to the adaptive RL system.
    Gracefully degrades to no-op if adaptive/ modules are unavailable.
    """

    def __init__(self):
        self.episode_mgr = None
        self.q_learner = None
        self.threshold_learner = None
        self.current_episode_id: Optional[str] = None
        self._portfolio_start: Optional[float] = None
        self._trade_count: int = 0
        self._pending_rewards: dict = {}  # symbol → entry info
        self._today_regime: str = "unknown"

        self._try_load()

    def _try_load(self):
        # Load Q-learning components
        try:
            sys.path.insert(0, str(_ADAPTIVE_DIR))
            from episode_manager import EpisodeManager
            from q_learner import QLearner
            self.episode_mgr = EpisodeManager()
            self.q_learner = QLearner()
            logger.info("✓ RL Episode Bridge active (EpisodeManager + QLearner)")
        except Exception as e:
            logger.warning(f"RL Episode Bridge disabled: {e}")

        # Load ThresholdLearner (independent — works even without adaptive/)
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from threshold_learner import ThresholdLearner
            self.threshold_learner = ThresholdLearner()
            logger.info("✓ ThresholdLearner active (RL-trained score threshold)")
        except Exception as e:
            logger.warning(f"ThresholdLearner disabled: {e}")

    @property
    def active(self) -> bool:
        return self.episode_mgr is not None

    # ──────────────────────────────────────────────────────────────────────────
    # Orchestrator hooks
    # ──────────────────────────────────────────────────────────────────────────

    def on_market_open(self, portfolio: dict):
        """Call at the start of the first market-open cycle each day."""
        # Detect market regime for threshold selection
        regime = self._detect_regime(portfolio)
        self._today_regime = regime

        # ThresholdLearner: select today's optimal threshold via Thompson Sampling
        if self.threshold_learner:
            try:
                threshold = self.threshold_learner.select_threshold(regime)
                logger.info(
                    f"RL Threshold → {threshold} "
                    f"(regime={regime}, learned via Thompson Sampling)"
                )
            except Exception as e:
                logger.warning(f"ThresholdLearner.select_threshold failed: {e}")

        if not self.active:
            return
        try:
            pv = float(portfolio.get("portfolio_value", 0))
            self._portfolio_start = pv
            self._trade_count = 0
            self._pending_rewards = {}
            today = datetime.now().strftime("%Y-%m-%d")
            self.current_episode_id = self.episode_mgr.episode_start(today, pv)
            logger.info(f"Episode {self.current_episode_id} started — portfolio ${pv:.2f}")
        except Exception as e:
            logger.debug(f"on_market_open failed: {e}")

    def on_trade(self, symbol: str, score: float, notional: float, portfolio: dict):
        """Call immediately after a successful buy order."""
        if not self.active or not self.current_episode_id:
            return
        try:
            pv = float(portfolio.get("portfolio_value", 0))
            start = self._portfolio_start or pv
            state = self._get_state(pv, start)

            # Map score to action label
            if score >= 80:
                action = "aggressive_buy"
            elif score >= 65:
                action = "moderate_buy"
            else:
                action = "moderate_buy"

            self.episode_mgr.episode_step(
                episode_id=self.current_episode_id,
                state=state,
                action=action,
                symbol=symbol,
                notional=notional,
                score=score,
            )

            # Track entry for reward calculation at exit
            self._pending_rewards[symbol] = {
                "entry_notional": notional,
                "entry_score": score,
                "entry_time": datetime.now().isoformat(),
            }

            self._trade_count += 1
            logger.debug(f"Episode step: {symbol} action={action} score={score:.1f}")
        except Exception as e:
            logger.debug(f"on_trade failed: {e}")

    def on_trade_closed(self, symbol: str, pnl_pct: float, portfolio: dict):
        """
        Call when a position is closed (execute_sell or zombie cleanup).
        pnl_pct: realized return, e.g. 0.05 = +5%, -0.12 = -12%
        """
        # ThresholdLearner: record outcome to update Beta distribution
        if self.threshold_learner:
            try:
                self.threshold_learner.record_outcome(
                    pnl_pct=pnl_pct,
                    regime=self._today_regime,
                )
            except Exception as e:
                logger.debug(f"ThresholdLearner.record_outcome failed: {e}")

        if not self.active or not self.current_episode_id:
            return
        try:
            # Reward = scaled P&L. Multiply by 100 so +5% = reward 5.0
            reward = pnl_pct * 100.0

            # Penalty for excess trades (PDT risk proxy)
            if self._trade_count > 3:
                reward -= 0.2 * (self._trade_count - 3)

            self.episode_mgr.record_reward(
                episode_id=self.current_episode_id,
                symbol=symbol,
                reward=reward,
                pnl_pct=pnl_pct,
            )

            outcome = "win" if pnl_pct > 0 else "loss"
            logger.info(
                f"Episode reward: {symbol} pnl={pnl_pct:.2%} → reward={reward:.2f} ({outcome})"
            )

            # Clean up pending entry
            self._pending_rewards.pop(symbol, None)

        except Exception as e:
            logger.debug(f"on_trade_closed failed: {e}")

    def on_market_close(self, portfolio: dict):
        """
        Call at end of last market-open cycle (or when market closes).
        Ends the episode, runs Monte Carlo Q-update, writes action back to config.
        """
        # Log threshold learning summary for the day
        if self.threshold_learner:
            try:
                logger.info(self.threshold_learner.summary(self._today_regime))
            except Exception as _e:
                logger.debug("non-critical error: %s", _e)

        if not self.active or not self.current_episode_id:
            return
        try:
            pv = float(portfolio.get("portfolio_value", 0))
            start = self._portfolio_start or pv
            episode_return = (pv - start) / start if start > 0 else 0.0

            # End episode
            self.episode_mgr.episode_end(
                episode_id=self.current_episode_id,
                final_value=pv,
                episode_return=episode_return,
            )

            # Run MC Q-update
            episode_data = None
            try:
                episode_data = self.episode_mgr.load_episode(self.current_episode_id)
                if episode_data:
                    self.q_learner.mc_update(episode_data)
                    logger.info(f"MC Q-update complete for episode {self.current_episode_id}")
            except Exception as e:
                logger.debug(f"Q-update failed: {e}")

            # Get Q-recommendation for next session
            try:
                state = self._get_state(pv, start)
                q_action = self.q_learner.get_action(state)
            except Exception:
                q_action = "moderate_buy"

            # Write multipliers to live_config.json
            self._write_rl_decision(q_action, episode_return)

            logger.info(
                f"Episode {self.current_episode_id} done | "
                f"return={episode_return:.2%} | trades={self._trade_count} | "
                f"Q→next: {q_action}"
            )
            self.current_episode_id = None

        except Exception as e:
            logger.debug(f"on_market_close failed: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_regime(self, portfolio: dict) -> str:
        """
        Detect current market regime.
        Tries the adaptive regime_detector; falls back to a simple heuristic.
        """
        try:
            sys.path.insert(0, str(_ADAPTIVE_DIR))
            from regime_detector import RegimeDetector
            detector = RegimeDetector()
            regime = detector.get_regime()
            if regime in ("bull", "bear", "neutral", "unknown"):
                return regime
        except Exception as _e:
            logger.debug("non-critical error: %s", _e)

        # Heuristic fallback: use portfolio P&L trend as a proxy
        try:
            pv = float(portfolio.get("portfolio_value", 0))
            unrealized = sum(
                float(p.get("unrealized_pl", 0))
                for p in portfolio.get("positions", [])
            )
            if pv > 0:
                pnl_pct = unrealized / pv
                if pnl_pct > 0.01:
                    return "bull"
                elif pnl_pct < -0.01:
                    return "bear"
        except Exception as _e:
            logger.debug("non-critical error: %s", _e)

        return "unknown"

    def _get_state(self, portfolio_value: float, start_value: float) -> str:
        """Build discretized state key for Q-table lookup."""
        try:
            return self.episode_mgr.get_current_state(
                portfolio_value=portfolio_value,
                start_value=start_value,
            )
        except Exception:
            return "unknown"

    def _write_rl_decision(self, action: str, episode_return: float):
        """Persist Q-action + multipliers to live_config.json."""
        try:
            sys.path.insert(0, str(_STRATEGY_DIR))
            from core.dynamic_config import cfg_update
            mults = _ACTION_MULTIPLIERS.get(action, {"trade_mult": 1.0, "size_mult": 1.0})
            cfg_update({
                "rl_action":            action,
                "rl_trade_multiplier":  mults["trade_mult"],
                "rl_size_multiplier":   mults["size_mult"],
                "rl_last_episode_return": round(episode_return, 4),
                "rl_updated_at":        datetime.now().isoformat(),
            })
            logger.info(
                f"RL config → action={action} "
                f"trade_mult={mults['trade_mult']} size_mult={mults['size_mult']}"
            )
        except Exception as e:
            logger.warning(f"RL config write failed: {e}")
