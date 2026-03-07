"""
Episode Manager — Treats each trading day as a complete RL episode.

Episode lifecycle:
  Market Open  → episode_start()  → record initial state
  Every 30 min → episode_step()   → record (state, action, reward, next_state)
  Market Close → episode_end()    → compute terminal reward, trigger learning

State space (discretized):
  - Regime: 5 levels (trending_up/down, high/low_vol, mean_reverting)
  - Portfolio heat: 4 levels (% of capital deployed)
  - Intraday PnL momentum: 3 levels (losing, flat, winning)
  - Time of day: 4 buckets (morning, midday, afternoon, close)
  Total: 5 × 4 × 3 × 4 = 240 discrete states

Action space:
  - aggressive_buy: significantly increase exposure
  - moderate_buy: small position adds
  - hold: maintain current positions
  - reduce: trim positions
  - defensive: tighten stops, avoid new trades

Reward function:
  - Primary: step PnL change (%)
  - Penalty: intraday drawdown
  - Penalty: excessive trades (PDT risk)
  - Bonus: capital preservation in down markets

Designed for Raspberry Pi: pure Python + numpy, no heavy deps.
"""

import json
import os
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger("adaptive.episodes")

EPISODES_DIR = os.path.join(os.path.dirname(__file__), "episodes")
EPISODE_INDEX_FILE = os.path.join(os.path.dirname(__file__), "episode_index.json")


# ═══════════════════════════════════════════════════════════════
# State discretization
# ═══════════════════════════════════════════════════════════════

REGIMES = ["trending_up", "trending_down", "high_volatility",
           "low_volatility", "mean_reverting"]

HEAT_LEVELS = [
    ("cold",        0.00, 0.25),   # <25% capital deployed
    ("warm",        0.25, 0.50),   # 25-50%
    ("hot",         0.50, 0.75),   # 50-75%
    ("overheated",  0.75, 1.01),   # >75%
]

PNL_MOMENTUM = [
    ("losing",  -999, -0.005),  # Down >0.5% on the day
    ("flat",    -0.005, 0.005), # Within ±0.5%
    ("winning",  0.005, 999),   # Up >0.5% on the day
]

TIME_BUCKETS = [
    ("morning",    9,  11),  # 9:30-11:00
    ("midday",    11,  13),  # 11:00-13:00
    ("afternoon", 13,  15),  # 13:00-15:00
    ("close",     15,  17),  # 15:00-16:00+
]

ACTIONS = ["aggressive_buy", "moderate_buy", "hold", "reduce", "defensive"]


def discretize_state(regime, portfolio_heat, intraday_pnl_pct, hour):
    """
    Convert continuous market/portfolio state into a discrete state tuple.

    Returns: (regime_idx, heat_idx, momentum_idx, time_idx) — hashable tuple
    """
    # Regime
    regime_idx = REGIMES.index(regime) if regime in REGIMES else 3  # default low_vol

    # Portfolio heat (% of buying power used)
    heat_idx = 0
    for i, (_, lo, hi) in enumerate(HEAT_LEVELS):
        if lo <= portfolio_heat < hi:
            heat_idx = i
            break

    # PnL momentum
    mom_idx = 1  # default flat
    for i, (_, lo, hi) in enumerate(PNL_MOMENTUM):
        if lo <= intraday_pnl_pct < hi:
            mom_idx = i
            break

    # Time bucket
    time_idx = 0
    for i, (_, start_h, end_h) in enumerate(TIME_BUCKETS):
        if start_h <= hour < end_h:
            time_idx = i
            break

    return (regime_idx, heat_idx, mom_idx, time_idx)


def state_to_str(state_tuple):
    """Human-readable state label."""
    ri, hi, mi, ti = state_tuple
    return (
        f"{REGIMES[ri]}|"
        f"{HEAT_LEVELS[hi][0]}|"
        f"{PNL_MOMENTUM[mi][0]}|"
        f"{TIME_BUCKETS[ti][0]}"
    )


# ═══════════════════════════════════════════════════════════════
# Episode class
# ═══════════════════════════════════════════════════════════════

class Episode:
    """A single trading day episode."""

    def __init__(self, date_str, initial_portfolio_value, initial_cash,
                 positions_count, regime="unknown"):
        self.date = date_str
        self.initial_value = initial_portfolio_value
        self.initial_cash = initial_cash
        self.positions_count = positions_count

        self.steps = []           # List of step dicts
        self.trades_today = 0     # Count of trades executed
        self.peak_value = initial_portfolio_value
        self.trough_value = initial_portfolio_value
        self.max_drawdown = 0.0

        self.initial_regime = regime
        self.terminal_value = None
        self.terminal_reward = None
        self.completed = False

        self._step_count = 0

    def add_step(self, state_tuple, action, portfolio_value, cash,
                 trades_this_step=0, signals=None, regime=None):
        """
        Record a single step within the episode.

        Called every ~30 minutes during market hours.
        """
        # Compute step reward
        prev_value = (
            self.steps[-1]["portfolio_value"]
            if self.steps else self.initial_value
        )
        step_pnl = portfolio_value - prev_value
        step_pnl_pct = step_pnl / prev_value if prev_value > 0 else 0

        # Track drawdown
        self.peak_value = max(self.peak_value, portfolio_value)
        self.trough_value = min(self.trough_value, portfolio_value)
        current_dd = (
            (self.peak_value - portfolio_value) / self.peak_value
            if self.peak_value > 0 else 0
        )
        self.max_drawdown = max(self.max_drawdown, current_dd)

        self.trades_today += trades_this_step

        # Reward shaping
        reward = self._compute_step_reward(
            step_pnl_pct, current_dd, trades_this_step, action
        )

        step = {
            "step": self._step_count,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "state": list(state_tuple),
            "state_label": state_to_str(state_tuple),
            "action": action,
            "portfolio_value": round(portfolio_value, 2),
            "cash": round(cash, 2),
            "step_pnl": round(step_pnl, 4),
            "step_pnl_pct": round(step_pnl_pct, 6),
            "reward": round(reward, 6),
            "cumulative_pnl_pct": round(
                (portfolio_value - self.initial_value) / self.initial_value
                if self.initial_value > 0 else 0, 6
            ),
            "drawdown": round(current_dd, 6),
            "trades_this_step": trades_this_step,
            "total_trades_today": self.trades_today,
            "regime": regime or self.initial_regime,
            "signals_snapshot": signals or {}
        }

        self.steps.append(step)
        self._step_count += 1
        return step

    def _compute_step_reward(self, pnl_pct, drawdown, trades, action):
        """
        Multi-component reward function.

        Components:
        1. PnL (primary): direct portfolio change
        2. Drawdown penalty: -2x for intraday drawdowns
        3. Trade penalty: small cost per trade (PDT awareness)
        4. Consistency bonus: reward for staying profitable
        5. Defensive bonus: reward for avoiding losses in bad regimes
        """
        reward = 0.0

        # 1. PnL component (scaled ×100 for readability)
        reward += pnl_pct * 100

        # 2. Drawdown penalty (exponential — large drawdowns hurt more)
        if drawdown > 0.01:  # >1% intraday drawdown
            reward -= (drawdown * 100) ** 1.5 * 0.1

        # 3. Trade penalty (discourages excessive trading / PDT risk)
        if self.trades_today > 2:  # 3rd+ trade in a day
            reward -= 0.5 * (self.trades_today - 2)

        # 4. Defensive action in losing conditions
        if pnl_pct < -0.005 and action in ("reduce", "defensive"):
            reward += 0.2  # Bonus for cutting losses

        # 5. Holding in winning conditions
        if pnl_pct > 0.005 and action == "hold":
            reward += 0.1  # Let winners run

        return reward

    def end(self, terminal_portfolio_value, terminal_cash):
        """
        Close the episode at market close.

        Computes terminal reward with risk-adjusted daily return.
        """
        self.terminal_value = terminal_portfolio_value
        self.completed = True

        # Daily PnL
        daily_pnl = terminal_portfolio_value - self.initial_value
        daily_pnl_pct = (
            daily_pnl / self.initial_value if self.initial_value > 0 else 0
        )

        # Risk-adjusted reward: PnL / (1 + max_drawdown)
        # This penalizes volatile paths even if they end positive
        risk_adj = daily_pnl_pct / (1 + self.max_drawdown * 10)

        # Terminal reward components
        self.terminal_reward = {
            "daily_pnl": round(daily_pnl, 4),
            "daily_pnl_pct": round(daily_pnl_pct, 6),
            "risk_adjusted_return": round(risk_adj, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "total_trades": self.trades_today,
            "steps": len(self.steps),
            "sharpe_proxy": round(self._sharpe_proxy(), 4),
        }

        logger.info(
            f"Episode {self.date} ended: "
            f"PnL ${daily_pnl:+.2f} ({daily_pnl_pct:+.2%}) | "
            f"MaxDD {self.max_drawdown:.2%} | "
            f"Trades: {self.trades_today} | Steps: {len(self.steps)}"
        )

        return self.terminal_reward

    def _sharpe_proxy(self):
        """Intraday Sharpe-like ratio from step returns."""
        if not np or len(self.steps) < 2:
            return 0
        returns = [s["step_pnl_pct"] for s in self.steps]
        mean_r = np.mean(returns)
        std_r = np.std(returns)
        if std_r == 0:
            return 0
        return float(mean_r / std_r)

    def to_dict(self):
        """Serialize episode for storage."""
        return {
            "date": self.date,
            "initial_value": round(self.initial_value, 2),
            "initial_cash": round(self.initial_cash, 2),
            "terminal_value": round(self.terminal_value, 2) if self.terminal_value else None,
            "initial_regime": self.initial_regime,
            "max_drawdown": round(self.max_drawdown, 6),
            "trades_today": self.trades_today,
            "completed": self.completed,
            "terminal_reward": self.terminal_reward,
            "steps": self.steps,
            "step_count": len(self.steps),
        }

    @classmethod
    def from_dict(cls, d):
        """Deserialize episode from storage."""
        ep = cls(
            date_str=d["date"],
            initial_portfolio_value=d["initial_value"],
            initial_cash=d.get("initial_cash", 0),
            positions_count=0,
            regime=d.get("initial_regime", "unknown")
        )
        ep.steps = d.get("steps", [])
        ep.trades_today = d.get("trades_today", 0)
        ep.max_drawdown = d.get("max_drawdown", 0)
        ep.completed = d.get("completed", False)
        ep.terminal_value = d.get("terminal_value")
        ep.terminal_reward = d.get("terminal_reward")
        ep._step_count = len(ep.steps)
        if ep.steps:
            values = [s["portfolio_value"] for s in ep.steps]
            ep.peak_value = max(values)
            ep.trough_value = min(values)
        return ep


# ═══════════════════════════════════════════════════════════════
# Episode Manager
# ═══════════════════════════════════════════════════════════════

class EpisodeManager:
    """
    Manages the episode lifecycle across trading days.

    Responsibilities:
    - Start/step/end episodes
    - Persist episodes to disk
    - Maintain episode index for fast lookups
    - Compute Monte Carlo returns for Q-learning
    - Generate training data for the Q-learner
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.current_episode = None
        self.index = self._load_index()

        # Ensure episodes directory exists
        os.makedirs(EPISODES_DIR, exist_ok=True)

    def _load_index(self):
        """Load episode index (date → summary)."""
        if os.path.exists(EPISODE_INDEX_FILE):
            try:
                with open(EPISODE_INDEX_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"episodes": {}, "total_episodes": 0, "total_steps": 0}

    def _save_index(self):
        try:
            tmp = EPISODE_INDEX_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.index, f, indent=2)
            os.replace(tmp, EPISODE_INDEX_FILE)
        except Exception as e:
            logger.error(f"Failed to save episode index: {e}")

    def _save_episode(self, episode):
        """Persist a single episode to its own file."""
        path = os.path.join(EPISODES_DIR, f"{episode.date}.json")
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(episode.to_dict(), f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.error(f"Failed to save episode {episode.date}: {e}")

    def load_episode(self, date_str):
        """Load a specific episode from disk."""
        path = os.path.join(EPISODES_DIR, f"{date_str}.json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return Episode.from_dict(json.load(f))
            except Exception as e:
                logger.error(f"Failed to load episode {date_str}: {e}")
        return None

    # ------------------------------------------------------------------
    # Episode lifecycle
    # ------------------------------------------------------------------

    def start_episode(self, portfolio_value, cash, positions_count, regime="unknown"):
        """
        Start a new trading day episode.

        Call at market open (9:30 AM ET) or first cycle of the day.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Check if episode already exists for today
        if self.current_episode and self.current_episode.date == today:
            logger.info(f"Episode {today} already in progress")
            return self.current_episode

        # Load existing if we're resuming after a restart
        existing = self.load_episode(today)
        if existing and not existing.completed:
            self.current_episode = existing
            logger.info(f"Resumed episode {today} ({len(existing.steps)} steps)")
            return self.current_episode

        # Create new episode
        self.current_episode = Episode(
            date_str=today,
            initial_portfolio_value=portfolio_value,
            initial_cash=cash,
            positions_count=positions_count,
            regime=regime
        )

        logger.info(
            f"Started episode {today}: ${portfolio_value:.2f} | "
            f"{positions_count} positions | Regime: {regime}"
        )
        return self.current_episode

    def step(self, portfolio_value, cash, regime, portfolio_heat,
             action="hold", trades_this_step=0, signals=None):
        """
        Record a step in the current episode.

        Call every ~30 minutes during market hours.
        """
        if not self.current_episode:
            logger.warning("No active episode — starting one")
            self.start_episode(portfolio_value, cash, 0, regime)

        # Compute intraday PnL %
        intraday_pnl_pct = (
            (portfolio_value - self.current_episode.initial_value)
            / self.current_episode.initial_value
            if self.current_episode.initial_value > 0 else 0
        )

        # Current hour (ET approximation — UTC-5)
        hour = (datetime.utcnow().hour - 5) % 24

        # Discretize state
        state = discretize_state(regime, portfolio_heat, intraday_pnl_pct, hour)

        # Record step
        step_data = self.current_episode.add_step(
            state_tuple=state,
            action=action,
            portfolio_value=portfolio_value,
            cash=cash,
            trades_this_step=trades_this_step,
            signals=signals,
            regime=regime
        )

        # Auto-save periodically
        if len(self.current_episode.steps) % 3 == 0:
            self._save_episode(self.current_episode)

        return step_data

    def end_episode(self, portfolio_value, cash):
        """
        End the current episode at market close.

        Triggers Monte Carlo return computation and persists.
        """
        if not self.current_episode:
            logger.warning("No active episode to end")
            return None

        terminal = self.current_episode.end(portfolio_value, cash)

        # Compute Monte Carlo returns (backward pass)
        mc_returns = self._compute_monte_carlo_returns(self.current_episode)

        # Save episode
        self._save_episode(self.current_episode)

        # Update index
        self.index["episodes"][self.current_episode.date] = {
            "pnl": terminal["daily_pnl"],
            "pnl_pct": terminal["daily_pnl_pct"],
            "risk_adj": terminal["risk_adjusted_return"],
            "max_dd": terminal["max_drawdown"],
            "trades": terminal["total_trades"],
            "steps": terminal["steps"],
            "sharpe": terminal["sharpe_proxy"],
            "regime": self.current_episode.initial_regime,
        }
        self.index["total_episodes"] = len(self.index["episodes"])
        self.index["total_steps"] += len(self.current_episode.steps)
        self._save_index()

        result = {
            "terminal": terminal,
            "mc_returns": mc_returns,
            "training_pairs": self._extract_training_pairs(
                self.current_episode, mc_returns
            )
        }

        self.current_episode = None
        return result

    def check_and_manage_episode(self, portfolio_value, cash, positions_count,
                                  regime, portfolio_heat, action="hold",
                                  trades_this_step=0, signals=None):
        """
        Smart lifecycle manager — call this every cycle and it handles everything.

        - Before market open: no-op
        - At market open: starts episode
        - During market hours: records step
        - After market close: ends episode
        - Weekends/holidays: no-op

        Returns: step data dict or None
        """
        now = datetime.utcnow()
        et_hour = (now.hour - 5) % 24  # Rough ET conversion
        weekday = now.weekday()  # 0=Mon, 6=Sun

        # Skip weekends
        if weekday >= 5:
            if self.current_episode and not self.current_episode.completed:
                # Market closed on weekend — end any open episode
                return self.end_episode(portfolio_value, cash)
            return None

        # Market hours: roughly 9:30-16:00 ET → 14:30-21:00 UTC
        market_open = (now.hour == 14 and now.minute >= 30) or (15 <= now.hour < 21)
        market_just_closed = now.hour == 21 and now.minute < 30

        if market_open:
            # Ensure episode is started
            if not self.current_episode or self.current_episode.completed:
                self.start_episode(portfolio_value, cash, positions_count, regime)

            # Record step
            return self.step(
                portfolio_value=portfolio_value,
                cash=cash,
                regime=regime,
                portfolio_heat=portfolio_heat,
                action=action,
                trades_this_step=trades_this_step,
                signals=signals
            )

        elif market_just_closed and self.current_episode and not self.current_episode.completed:
            # Market just closed — end episode
            return self.end_episode(portfolio_value, cash)

        return None

    # ------------------------------------------------------------------
    # Monte Carlo returns
    # ------------------------------------------------------------------

    def _compute_monte_carlo_returns(self, episode, gamma=0.95):
        """
        Compute discounted Monte Carlo returns for each step.

        G_t = r_t + γ*r_{t+1} + γ²*r_{t+2} + ... + γ^(T-t)*R_T

        Where R_T includes the terminal reward bonus/penalty.
        """
        if not episode.steps:
            return []

        # Terminal bonus based on daily PnL
        terminal_bonus = 0
        if episode.terminal_reward:
            daily_pnl_pct = episode.terminal_reward.get("daily_pnl_pct", 0)
            # Scale terminal bonus: +5 for a great day, -5 for a bad day
            terminal_bonus = daily_pnl_pct * 500

        # Backward pass
        returns = [0.0] * len(episode.steps)
        G = terminal_bonus

        for t in range(len(episode.steps) - 1, -1, -1):
            r = episode.steps[t]["reward"]
            G = r + gamma * G
            returns[t] = round(G, 6)

        return returns

    def _extract_training_pairs(self, episode, mc_returns):
        """
        Extract (state, action, return) training pairs from a completed episode.

        These are fed to the Q-learner for value function updates.
        """
        if not episode.steps or not mc_returns:
            return []

        pairs = []
        for i, step in enumerate(episode.steps):
            if i < len(mc_returns):
                pairs.append({
                    "state": tuple(step["state"]),
                    "action": step["action"],
                    "mc_return": mc_returns[i],
                    "step_reward": step["reward"],
                    "date": episode.date,
                    "step_num": i
                })
        return pairs

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_episode_stats(self, last_n=30):
        """Summary statistics over recent episodes."""
        episodes = sorted(
            self.index.get("episodes", {}).items(),
            key=lambda x: x[0],
            reverse=True
        )[:last_n]

        if not episodes:
            return {"message": "No completed episodes yet"}

        pnls = [e["pnl_pct"] for _, e in episodes]
        wins = sum(1 for p in pnls if p > 0)
        losses = len(pnls) - wins

        result = {
            "episodes": len(episodes),
            "win_rate": round(wins / len(episodes), 4) if episodes else 0,
            "avg_daily_pnl_pct": round(float(np.mean(pnls)), 6) if np else 0,
            "best_day": max(pnls) if pnls else 0,
            "worst_day": min(pnls) if pnls else 0,
            "total_return_pct": round(sum(pnls), 6),
            "avg_max_drawdown": round(
                float(np.mean([e["max_dd"] for _, e in episodes])), 6
            ) if np else 0,
            "avg_trades_per_day": round(
                float(np.mean([e["trades"] for _, e in episodes])), 1
            ) if np else 0,
            "avg_sharpe": round(
                float(np.mean([e.get("sharpe", 0) for _, e in episodes])), 4
            ) if np else 0,
        }

        # Regime breakdown
        regime_stats = defaultdict(lambda: {"count": 0, "total_pnl": 0})
        for _, e in episodes:
            r = e.get("regime", "unknown")
            regime_stats[r]["count"] += 1
            regime_stats[r]["total_pnl"] += e["pnl_pct"]

        result["regime_breakdown"] = {
            r: {
                "days": d["count"],
                "avg_pnl": round(d["total_pnl"] / d["count"], 6)
            }
            for r, d in regime_stats.items()
        }

        return result

    def get_recent_episodes(self, n=5):
        """Load the N most recent episodes."""
        dates = sorted(self.index.get("episodes", {}).keys(), reverse=True)[:n]
        return [self.load_episode(d) for d in dates if self.load_episode(d)]
