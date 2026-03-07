"""
Q-Learner — Tabular Q-learning with Monte Carlo episode updates.

State space: 240 discrete states (regime × heat × momentum × time)
Action space: 5 actions (aggressive_buy, moderate_buy, hold, reduce, defensive)
Q-table: 240 × 5 = 1,200 entries — trivially small, pure dict

Learning modes:
1. Monte Carlo (end of episode): update Q from full episode returns
2. TD(0) (each step): online temporal-difference updates
3. Both (default): MC for primary learning, TD for faster adaptation

The Q-table directly answers: "Given the current market state, what should I do?"

Designed for Raspberry Pi: pure Python + numpy, ~1KB Q-table.
"""

import json
import os
import logging
import random
from datetime import datetime
from collections import defaultdict

try:
    import numpy as np
except ImportError:
    np = None

from episode_manager import ACTIONS, REGIMES, HEAT_LEVELS, PNL_MOMENTUM, TIME_BUCKETS

logger = logging.getLogger("adaptive.qlearner")

Q_TABLE_FILE = os.path.join(os.path.dirname(__file__), "q_table.json")
Q_STATS_FILE = os.path.join(os.path.dirname(__file__), "q_stats.json")


class QLearner:
    """
    Tabular Q-learning agent for daily trading episodes.

    Q(s, a) ← Q(s, a) + α * [target - Q(s, a)]

    Where target depends on update mode:
    - MC: target = G_t (Monte Carlo return from episode)
    - TD(0): target = r + γ * max_a' Q(s', a')
    """

    def __init__(self, config=None):
        self.config = config or {}

        # Hyperparameters
        lc = self.config.get("learning", {})
        self.alpha = lc.get("q_learning_rate", 0.1)       # Learning rate
        self.gamma = lc.get("q_discount_factor", 0.95)     # Discount factor
        self.epsilon = lc.get("q_epsilon", 0.15)           # Exploration rate
        self.epsilon_decay = lc.get("q_epsilon_decay", 0.995)  # Decay per episode
        self.epsilon_min = lc.get("q_epsilon_min", 0.05)   # Minimum exploration
        self.min_episodes = lc.get("q_min_episodes", 3)    # Before trusting Q-values

        self.q_table = {}     # {state_str: {action: q_value}}
        self.visit_counts = {}  # {state_str: {action: count}}
        self.stats = {
            "total_updates": 0,
            "episodes_learned": 0,
            "epsilon": self.epsilon,
            "best_action_overrides": 0,
            "exploration_actions": 0,
            "created": datetime.utcnow().isoformat() + "Z"
        }

        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        """Load Q-table and stats from disk."""
        if os.path.exists(Q_TABLE_FILE):
            try:
                with open(Q_TABLE_FILE, "r") as f:
                    data = json.load(f)
                self.q_table = data.get("q_table", {})
                self.visit_counts = data.get("visit_counts", {})
                logger.info(
                    f"Loaded Q-table: {len(self.q_table)} states, "
                    f"{sum(len(v) for v in self.q_table.values())} entries"
                )
            except Exception as e:
                logger.warning(f"Failed to load Q-table: {e}")

        if os.path.exists(Q_STATS_FILE):
            try:
                with open(Q_STATS_FILE, "r") as f:
                    self.stats = json.load(f)
                self.epsilon = self.stats.get("epsilon", self.epsilon)
            except Exception:
                pass

    def _save(self):
        """Persist Q-table and stats to disk."""
        try:
            data = {
                "q_table": self.q_table,
                "visit_counts": self.visit_counts,
                "saved_at": datetime.utcnow().isoformat() + "Z"
            }
            tmp = Q_TABLE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, Q_TABLE_FILE)
        except Exception as e:
            logger.error(f"Failed to save Q-table: {e}")

        try:
            self.stats["epsilon"] = self.epsilon
            self.stats["last_save"] = datetime.utcnow().isoformat() + "Z"
            with open(Q_STATS_FILE, "w") as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save Q-stats: {e}")

    # ------------------------------------------------------------------
    # Q-value access
    # ------------------------------------------------------------------

    def _state_key(self, state_tuple):
        """Convert state tuple to string key for dict storage."""
        return str(state_tuple)

    def get_q(self, state_tuple, action):
        """Get Q(s, a). Returns 0 for unseen state-action pairs."""
        key = self._state_key(state_tuple)
        return self.q_table.get(key, {}).get(action, 0.0)

    def get_all_q(self, state_tuple):
        """Get Q-values for all actions in state s."""
        key = self._state_key(state_tuple)
        return {a: self.q_table.get(key, {}).get(a, 0.0) for a in ACTIONS}

    def set_q(self, state_tuple, action, value):
        """Set Q(s, a)."""
        key = self._state_key(state_tuple)
        if key not in self.q_table:
            self.q_table[key] = {}
        self.q_table[key][action] = round(value, 6)

        # Track visits
        if key not in self.visit_counts:
            self.visit_counts[key] = {}
        self.visit_counts[key][action] = self.visit_counts[key].get(action, 0) + 1

    # ------------------------------------------------------------------
    # Action selection
    # ------------------------------------------------------------------

    def select_action(self, state_tuple, valid_actions=None):
        """
        ε-greedy action selection.

        With probability ε: explore (random action)
        With probability 1-ε: exploit (best Q-value action)

        Returns: (action, was_exploration)
        """
        if valid_actions is None:
            valid_actions = ACTIONS

        # Not enough episodes — default to conservative
        if self.stats.get("episodes_learned", 0) < self.min_episodes:
            return "hold", False

        # ε-greedy
        if random.random() < self.epsilon:
            action = random.choice(valid_actions)
            self.stats["exploration_actions"] = (
                self.stats.get("exploration_actions", 0) + 1
            )
            return action, True

        # Exploit: pick action with highest Q-value
        q_values = self.get_all_q(state_tuple)
        valid_q = {a: q_values.get(a, 0.0) for a in valid_actions}

        # If all Q-values are 0 (no data), return hold
        if all(v == 0 for v in valid_q.values()):
            return "hold", False

        best_action = max(valid_q, key=valid_q.get)
        self.stats["best_action_overrides"] = (
            self.stats.get("best_action_overrides", 0) + 1
        )
        return best_action, False

    def get_recommended_action(self, state_tuple):
        """
        Get the recommended action with confidence info.

        Returns dict with action, Q-values, confidence, and reasoning.
        """
        q_values = self.get_all_q(state_tuple)
        key = self._state_key(state_tuple)
        visits = self.visit_counts.get(key, {})
        total_visits = sum(visits.values()) if visits else 0

        # Confidence based on visit count
        confidence = min(1.0, total_visits / (self.min_episodes * len(ACTIONS)))

        # Sort actions by Q-value
        sorted_actions = sorted(q_values.items(), key=lambda x: x[1], reverse=True)
        best_action, best_q = sorted_actions[0]
        worst_action, worst_q = sorted_actions[-1]

        # Q-value spread (how differentiated are the actions?)
        spread = best_q - worst_q

        return {
            "action": best_action,
            "q_value": round(best_q, 4),
            "confidence": round(confidence, 4),
            "q_spread": round(spread, 4),
            "all_q_values": {a: round(v, 4) for a, v in sorted_actions},
            "visits": total_visits,
            "reasoning": self._explain_action(state_tuple, best_action, q_values)
        }

    def _explain_action(self, state_tuple, action, q_values):
        """Generate a human-readable explanation for the recommended action."""
        from episode_manager import state_to_str
        state_label = state_to_str(state_tuple)

        q_sorted = sorted(q_values.items(), key=lambda x: x[1], reverse=True)
        top_3 = ", ".join(f"{a}={v:.3f}" for a, v in q_sorted[:3])

        return (
            f"State [{state_label}]: {action} "
            f"(Q: {top_3})"
        )

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def update_from_episode(self, training_pairs):
        """
        Monte Carlo update: use full episode returns to update Q-values.

        training_pairs: list of {state, action, mc_return} from EpisodeManager
        """
        if not training_pairs:
            return

        for pair in training_pairs:
            state = tuple(pair["state"]) if isinstance(pair["state"], list) else pair["state"]
            action = pair["action"]
            G = pair["mc_return"]

            # Q(s,a) ← Q(s,a) + α * [G - Q(s,a)]
            current_q = self.get_q(state, action)
            new_q = current_q + self.alpha * (G - current_q)
            self.set_q(state, action, new_q)
            self.stats["total_updates"] = self.stats.get("total_updates", 0) + 1

        # Decay epsilon after each episode
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        self.stats["episodes_learned"] = self.stats.get("episodes_learned", 0) + 1

        self._save()

        logger.info(
            f"MC update: {len(training_pairs)} state-action pairs | "
            f"ε={self.epsilon:.3f} | "
            f"Episodes: {self.stats['episodes_learned']}"
        )

    def td_update(self, state, action, reward, next_state):
        """
        TD(0) update: online learning from each step.

        Q(s,a) ← Q(s,a) + α * [r + γ * max_a' Q(s',a') - Q(s,a)]
        """
        current_q = self.get_q(state, action)

        # Max Q-value at next state
        next_q_values = self.get_all_q(next_state)
        max_next_q = max(next_q_values.values()) if next_q_values else 0

        # TD target
        target = reward + self.gamma * max_next_q
        new_q = current_q + self.alpha * (target - current_q)

        self.set_q(state, action, new_q)
        self.stats["total_updates"] = self.stats.get("total_updates", 0) + 1

    # ------------------------------------------------------------------
    # Analysis & reporting
    # ------------------------------------------------------------------

    def get_policy_summary(self):
        """
        Summarize the learned policy: for each state with data,
        what's the best action?
        """
        policy = {}
        for state_key, actions in self.q_table.items():
            if actions:
                best_action = max(actions, key=actions.get)
                policy[state_key] = {
                    "best_action": best_action,
                    "q_value": round(actions[best_action], 4),
                    "visits": sum(
                        self.visit_counts.get(state_key, {}).values()
                    )
                }
        return policy

    def get_state_action_heatmap(self):
        """
        Generate a regime × action heatmap of average Q-values.
        Shows which actions work best in which regimes.
        """
        heatmap = {}
        for ri, regime in enumerate(REGIMES):
            heatmap[regime] = {}
            for action in ACTIONS:
                q_vals = []
                for state_key, actions in self.q_table.items():
                    # Parse state key to check regime
                    try:
                        state = eval(state_key)
                        if state[0] == ri and action in actions:
                            q_vals.append(actions[action])
                    except Exception:
                        pass
                heatmap[regime][action] = (
                    round(float(np.mean(q_vals)), 4)
                    if np and q_vals else 0
                )
        return heatmap

    def report(self):
        """Human-readable report of the Q-learner state."""
        lines = [
            "═══ Q-Learner Report ═══",
            f"States explored: {len(self.q_table)}",
            f"Total updates: {self.stats.get('total_updates', 0)}",
            f"Episodes learned: {self.stats.get('episodes_learned', 0)}",
            f"Epsilon (exploration): {self.epsilon:.3f}",
            f"Exploit/Explore ratio: "
            f"{self.stats.get('best_action_overrides', 0)}/"
            f"{self.stats.get('exploration_actions', 0)}",
            ""
        ]

        # Top policies
        policy = self.get_policy_summary()
        if policy:
            lines.append("Learned policies (top 10 by visits):")
            sorted_p = sorted(
                policy.items(),
                key=lambda x: x[1]["visits"],
                reverse=True
            )[:10]
            for state_key, data in sorted_p:
                lines.append(
                    f"  {state_key}: {data['best_action']} "
                    f"(Q={data['q_value']:.3f}, visits={data['visits']})"
                )
        else:
            lines.append("No policies learned yet (need completed episodes)")

        # Regime heatmap
        heatmap = self.get_state_action_heatmap()
        if any(any(v != 0 for v in actions.values()) for actions in heatmap.values()):
            lines.append("\nRegime × Action Q-values:")
            lines.append(f"  {'':15s} " + " ".join(f"{a:>13s}" for a in ACTIONS))
            for regime, actions in heatmap.items():
                vals = " ".join(f"{actions.get(a, 0):>13.3f}" for a in ACTIONS)
                lines.append(f"  {regime:15s} {vals}")

        return "\n".join(lines)
