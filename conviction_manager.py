"""
Conviction Manager — Dynamic thesis-driven position management.

The quant brain says "sell." Your gut says "hold." This module bridges that gap
with a structured framework that respects conviction plays while enforcing the
cardinal rule: being too early IS the same as being wrong.

Core idea: Every conviction position has a SCORE (0-100) that starts where you
set it and evolves based on:
  - Time decay (conviction erodes — catalysts have expiration dates)
  - Sentiment shifts (news, earnings, analyst reports)
  - Price action (is reality confirming or denying the thesis?)
  - Volume patterns (smart money accumulating or distributing?)
  - Market context (is this a storm or a sinking ship?)

Thresholds drive behavior:
  80-100  STRONG      → Accumulate on dips, wide stops, full patience
  60-79   MODERATE    → Hold position, no new buys, standard stops
  40-59   WEAKENING   → Tighten stops, prepare exit plan, reduce on bounces
  20-39   FADING      → Systematic exit begins, sell into strength
  0-19    DEAD        → Liquidate, thesis failed, normal rules resume

Integration: Orchestrator checks conviction_manager before risk_fortress
for flagged symbols. Conviction positions get custom rules instead of
default concentration/zombie limits.
"""

import os
import json
import math
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("conviction_manager")

BASE_DIR = Path(__file__).parent
CONVICTIONS_FILE = BASE_DIR / "state" / "convictions.json"
CONVICTION_HISTORY_FILE = BASE_DIR / "state" / "conviction_history.json"
SENTIMENT_CACHE_FILE = BASE_DIR / "state" / "sentiment_cache.json"

# ─── Alpaca helpers ───────────────────────────────────────────────────────────

ALPACA_DATA = "https://data.alpaca.markets"
ALPACA_BASE = "https://api.alpaca.markets"


def _get_keys():
    key = os.getenv("ALPACA_API_LIVE_KEY") or os.getenv("APCA_API_KEY_ID", "")
    sec = os.getenv("ALPACA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY", "")
    return key, sec


def _headers():
    k, s = _get_keys()
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def _api_get(url, params=None, timeout=10):
    if not requests:
        return None
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"API call failed: {e}")
        return None


# ─── Conviction Data Model ────────────────────────────────────────────────────

def _now_ts():
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts_str):
    """Parse ISO timestamp, handle both aware and naive."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _days_elapsed(start_ts, end_ts=None):
    """Days between two timestamps."""
    start = _parse_ts(start_ts)
    end = _parse_ts(end_ts) if end_ts else datetime.now(timezone.utc)
    return max(0, (end - start).total_seconds() / 86400)


DEFAULT_CONVICTION = {
    # Identity
    "symbol": "",
    "thesis": "",                    # Human-readable thesis ("GME acquisition by large tech co")
    "catalyst": "",                  # Specific expected event ("Acquisition announcement")
    "catalyst_type": "event",        # event | earnings | macro | technical | squeeze

    # Conviction parameters
    "base_score": 75,                # Starting conviction (0-100)
    "current_score": 75,             # Dynamic score (updated each cycle)
    "manual_adjustment": 0,          # User can nudge +/- at any time

    # Timeframe — the "too early = wrong" guard
    "set_date": "",                  # When conviction was established
    "catalyst_deadline": "",         # When catalyst should materialize by
    "max_hold_days": 90,             # Absolute maximum hold (even if score is high)
    "half_life_days": 30,            # After this many days, time decay accelerates

    # Price levels
    "entry_price": 0.0,             # Average entry (can be filled from Alpaca)
    "target_price": 0.0,            # Exit target if thesis plays out
    "max_pain_price": 0.0,          # Absolute floor — abandon thesis below this
    "current_price": 0.0,           # Last known price

    # Accumulation rules
    "accumulate_on_dips": True,      # Buy dips while conviction is STRONG
    "dip_threshold_pct": -5.0,       # Buy when stock drops this % intraday
    "max_add_per_dip": 0.0,         # Max $ to add per dip buy (0 = auto from config)
    "max_position_pct": 40.0,       # Max portfolio % for this conviction play
    "min_days_between_adds": 2,      # Don't add every single day

    # Risk overrides (these replace normal risk_fortress limits)
    "override_zombie_kill": True,    # Don't auto-liquidate as zombie
    "override_concentration": True,  # Allow above normal 20% limit
    "override_stop_loss": True,      # Use conviction-specific stops instead
    "stop_loss_pct": -25.0,         # Wide stop for conviction plays

    # State tracking
    "phase": "ACCUMULATING",         # ACCUMULATING | HOLDING | TRIGGERED | EXITING | EXPIRED | CLOSED
    "score_history": [],             # [{ts, score, reason}] — audit trail
    "last_add_date": "",             # When we last added to position
    "total_added": 0.0,             # Total $ added since conviction set
    "sentiment_trend": 0.0,         # Running sentiment EMA (-1 to +1)
    "volume_trend": 0.0,            # Relative volume trend (>1 = above avg)
    "news_events": [],              # [{ts, headline, sentiment, impact}]

    # Metadata
    "notes": "",                     # Free-form notes
    "created_at": "",
    "updated_at": "",
    "closed_at": "",
    "close_reason": "",
}


# ─── Core Conviction Manager ─────────────────────────────────────────────────

class ConvictionManager:
    """
    Manages conviction positions — the contrarian plays where you're
    betting against the crowd with a specific thesis and timeline.
    """

    def __init__(self, config=None):
        self.config = config or self._default_config()
        self.convictions = {}  # symbol -> conviction dict
        self.history = []       # closed convictions for learning
        self._load()

    def _default_config(self):
        return {
            # Time decay
            "base_decay_per_day": 0.5,        # Points lost per day (base rate)
            "accelerated_decay_mult": 2.5,    # Multiplier after half-life
            "deadline_decay_mult": 5.0,       # Multiplier in final week before deadline

            # Sentiment impact
            "sentiment_weight": 12.0,          # Max points from single sentiment event
            "earnings_beat_boost": 15.0,       # Score boost for earnings beat
            "earnings_miss_penalty": -20.0,    # Score hit for earnings miss
            "sentiment_ema_alpha": 0.3,        # EMA smoothing for sentiment trend

            # Volume signals
            "accumulation_boost": 5.0,         # Score boost for accumulation pattern
            "distribution_penalty": -8.0,      # Score penalty for distribution
            "volume_lookback_days": 20,        # Days for volume average

            # Price action
            "price_confirm_boost": 3.0,        # Boost when price moves toward target
            "price_deny_penalty": -4.0,        # Penalty when moving away
            "momentum_lookback_days": 5,       # Short-term price momentum window

            # Storm detection
            "storm_correlation_threshold": 0.6,  # If stock drops WITH market, it's a storm
            "storm_dampening": 0.3,              # Reduce penalties by this factor during storms
            "market_proxy": "SPY",               # Benchmark for storm detection

            # Accumulation
            "default_add_amount": 25.0,        # $ to add per dip (if not specified)
            "max_total_adds_pct": 50.0,        # Max total $ added as % of original position

            # Phase thresholds
            "strong_threshold": 80,
            "moderate_threshold": 60,
            "weakening_threshold": 40,
            "fading_threshold": 20,

            # Auto-close
            "auto_close_score": 10,            # Close position below this score
            "max_concurrent_convictions": 3,   # Don't have too many at once
        }

    # ─── Persistence ──────────────────────────────────────────────────────

    def _load(self):
        """Load active convictions and history from disk."""
        if CONVICTIONS_FILE.exists():
            try:
                with open(CONVICTIONS_FILE) as f:
                    self.convictions = json.load(f)
                logger.info(f"Loaded {len(self.convictions)} active convictions")
            except Exception as e:
                logger.error(f"Failed to load convictions: {e}")
                self.convictions = {}

        if CONVICTION_HISTORY_FILE.exists():
            try:
                with open(CONVICTION_HISTORY_FILE) as f:
                    self.history = json.load(f)
            except Exception:
                self.history = []

    def _save(self):
        """Persist to disk."""
        CONVICTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(CONVICTIONS_FILE, "w") as f:
                json.dump(self.convictions, f, indent=2, default=str)
            with open(CONVICTION_HISTORY_FILE, "w") as f:
                json.dump(self.history[-100:], f, indent=2, default=str)  # Keep last 100
        except Exception as e:
            logger.error(f"Failed to save convictions: {e}")

    # ─── Conviction CRUD ──────────────────────────────────────────────────

    def set_conviction(self, symbol, thesis, catalyst, catalyst_deadline,
                       target_price, max_pain_price, base_score=75,
                       catalyst_type="event", max_position_pct=40.0,
                       entry_price=None, notes="", **kwargs):
        """
        Establish a new conviction position.

        Args:
            symbol: Ticker (e.g., "GME")
            thesis: Why you believe ("Acquisition by large tech company")
            catalyst: Specific event ("Acquisition announcement")
            catalyst_deadline: ISO date — when catalyst must happen by
            target_price: Exit target if thesis plays out
            max_pain_price: Absolute abandon price
            base_score: Starting conviction 0-100
            catalyst_type: event|earnings|macro|technical|squeeze
            max_position_pct: Max portfolio allocation for this play
            entry_price: Average entry (auto-fetched if None)
            notes: Free-form notes
        """
        if len(self.convictions) >= self.config["max_concurrent_convictions"]:
            if symbol not in self.convictions:
                logger.warning(f"Max {self.config['max_concurrent_convictions']} concurrent convictions. "
                             f"Close one before adding {symbol}.")
                return None

        now = _now_ts()
        conv = {**DEFAULT_CONVICTION}
        conv.update({
            "symbol": symbol.upper(),
            "thesis": thesis,
            "catalyst": catalyst,
            "catalyst_type": catalyst_type,
            "catalyst_deadline": catalyst_deadline,
            "base_score": min(100, max(0, base_score)),
            "current_score": min(100, max(0, base_score)),
            "set_date": now,
            "target_price": float(target_price),
            "max_pain_price": float(max_pain_price),
            "max_position_pct": max_position_pct,
            "entry_price": float(entry_price) if entry_price else 0.0,
            "notes": notes,
            "created_at": now,
            "updated_at": now,
            "phase": "ACCUMULATING" if base_score >= self.config["strong_threshold"] else "HOLDING",
            "score_history": [{"ts": now, "score": base_score, "reason": "Initial conviction set"}],
        })
        # Apply any extra kwargs
        for k, v in kwargs.items():
            if k in conv:
                conv[k] = v

        # Auto-fetch entry price from Alpaca if not provided
        if conv["entry_price"] == 0.0:
            conv["entry_price"] = self._fetch_current_price(symbol) or 0.0
            conv["current_price"] = conv["entry_price"]

        self.convictions[symbol.upper()] = conv
        self._save()
        logger.info(f"Conviction SET: {symbol} @ score {base_score} — {thesis}")
        return conv

    def update_conviction(self, symbol, **updates):
        """Manually update conviction parameters."""
        symbol = symbol.upper()
        if symbol not in self.convictions:
            logger.warning(f"No conviction for {symbol}")
            return None

        conv = self.convictions[symbol]
        allowed_updates = [
            "thesis", "catalyst", "catalyst_deadline", "target_price",
            "max_pain_price", "max_position_pct", "manual_adjustment",
            "notes", "accumulate_on_dips", "dip_threshold_pct",
            "max_add_per_dip", "stop_loss_pct", "base_score",
            "max_hold_days", "half_life_days", "phase",
        ]
        changes = []
        for k, v in updates.items():
            if k in allowed_updates:
                old = conv.get(k)
                conv[k] = v
                changes.append(f"{k}: {old} → {v}")

        if changes:
            conv["updated_at"] = _now_ts()
            conv["score_history"].append({
                "ts": _now_ts(),
                "score": conv["current_score"],
                "reason": f"Manual update: {', '.join(changes)}"
            })
            self._save()
            logger.info(f"Conviction UPDATED {symbol}: {', '.join(changes)}")
        return conv

    def close_conviction(self, symbol, reason="Manual close"):
        """Close a conviction — move to history."""
        symbol = symbol.upper()
        if symbol not in self.convictions:
            return None

        conv = self.convictions.pop(symbol)
        conv["phase"] = "CLOSED"
        conv["closed_at"] = _now_ts()
        conv["close_reason"] = reason
        conv["score_history"].append({
            "ts": _now_ts(),
            "score": conv["current_score"],
            "reason": f"CLOSED: {reason}"
        })
        self.history.append(conv)
        self._save()
        logger.info(f"Conviction CLOSED: {symbol} — {reason}")
        return conv

    def get_conviction(self, symbol):
        """Get conviction for a symbol, or None."""
        return self.convictions.get(symbol.upper())

    def is_conviction_symbol(self, symbol):
        """Check if symbol has active conviction."""
        return symbol.upper() in self.convictions

    def get_active_convictions(self):
        """Return all active convictions."""
        return dict(self.convictions)

    # ─── Dynamic Score Updates ────────────────────────────────────────────

    def run_update_cycle(self, portfolio_value=None, positions=None):
        """
        Run a full update cycle for all active convictions.
        Call this every 30 minutes from the orchestrator.

        Updates:
          1. Current prices
          2. Time decay
          3. Price action signals
          4. Volume analysis
          5. Storm detection (market context)
          6. Phase transitions
          7. Accumulation signals

        Returns list of actions to take.
        """
        actions = []

        for symbol, conv in list(self.convictions.items()):
            try:
                result = self._update_single(symbol, conv, portfolio_value, positions)
                if result:
                    actions.extend(result)
            except Exception as e:
                logger.error(f"Error updating conviction {symbol}: {e}")

        self._save()
        return actions

    def _update_single(self, symbol, conv, portfolio_value=None, positions=None):
        """Update a single conviction position. Returns list of actions."""
        actions = []
        score_changes = []
        old_score = conv["current_score"]

        # 1. Fetch current price
        price = self._fetch_current_price(symbol)
        if price:
            conv["current_price"] = price

        # 2. Time decay — the "too early = wrong" enforcement
        time_delta = self._calc_time_decay(conv)
        if time_delta != 0:
            score_changes.append(("time_decay", time_delta))

        # 3. Price action — is reality confirming or denying the thesis?
        price_delta = self._calc_price_signal(conv)
        if price_delta != 0:
            score_changes.append(("price_action", price_delta))

        # 4. Volume analysis — accumulation vs distribution
        vol_delta = self._calc_volume_signal(symbol, conv)
        if vol_delta != 0:
            score_changes.append(("volume", vol_delta))

        # 5. Storm detection — dampen penalties if whole market is down
        storm_factor = self._detect_storm(symbol)

        # Apply changes with storm dampening
        total_delta = 0
        for source, delta in score_changes:
            if delta < 0 and storm_factor > 0:
                # Reduce penalties during storms
                adjusted = delta * (1 - storm_factor * self.config["storm_dampening"])
                total_delta += adjusted
                logger.debug(f"{symbol} {source}: {delta:.1f} → {adjusted:.1f} (storm dampened)")
            else:
                total_delta += delta

        # Add manual adjustment
        total_delta += conv.get("manual_adjustment", 0)
        conv["manual_adjustment"] = 0  # Reset after applying

        # Update score (clamped 0-100)
        new_score = max(0, min(100, old_score + total_delta))
        conv["current_score"] = round(new_score, 1)
        conv["updated_at"] = _now_ts()

        if abs(total_delta) >= 1.0:
            reasons = [f"{src}:{d:+.1f}" for src, d in score_changes]
            if storm_factor > 0:
                reasons.append(f"storm:{storm_factor:.0%}")
            conv["score_history"].append({
                "ts": _now_ts(),
                "score": conv["current_score"],
                "reason": ", ".join(reasons)
            })
            # Keep history manageable
            if len(conv["score_history"]) > 200:
                conv["score_history"] = conv["score_history"][-150:]

        # 6. Phase transitions
        phase_action = self._update_phase(symbol, conv, portfolio_value, positions)
        if phase_action:
            actions.append(phase_action)

        # 7. Max pain check — absolute floor
        if price and conv["max_pain_price"] > 0 and price <= conv["max_pain_price"]:
            actions.append({
                "type": "ABANDON",
                "symbol": symbol,
                "reason": f"Price ${price:.2f} hit max pain ${conv['max_pain_price']:.2f}",
                "urgency": "CRITICAL"
            })
            conv["phase"] = "EXITING"

        # 8. Deadline check
        if conv["catalyst_deadline"]:
            deadline = _parse_ts(conv["catalyst_deadline"])
            now = datetime.now(timezone.utc)
            if now > deadline and conv["phase"] not in ("TRIGGERED", "EXITING", "CLOSED"):
                actions.append({
                    "type": "DEADLINE_EXPIRED",
                    "symbol": symbol,
                    "reason": f"Catalyst deadline {conv['catalyst_deadline']} passed without trigger",
                    "urgency": "HIGH"
                })
                conv["phase"] = "EXPIRED"

        # 9. Max hold check
        days_held = _days_elapsed(conv["set_date"])
        if days_held > conv["max_hold_days"] and conv["phase"] not in ("TRIGGERED", "EXITING", "CLOSED"):
            actions.append({
                "type": "MAX_HOLD_EXCEEDED",
                "symbol": symbol,
                "reason": f"Held {days_held:.0f} days, max is {conv['max_hold_days']}",
                "urgency": "HIGH"
            })
            conv["phase"] = "EXPIRED"

        # 10. Accumulation signal
        if conv["phase"] == "ACCUMULATING" and conv["accumulate_on_dips"]:
            acc_action = self._check_accumulation(symbol, conv, portfolio_value, positions)
            if acc_action:
                actions.append(acc_action)

        # Log significant changes
        if abs(new_score - old_score) >= 3:
            logger.info(f"Conviction {symbol}: {old_score:.0f} → {new_score:.0f} "
                       f"(Δ{total_delta:+.1f}) phase={conv['phase']}")

        return actions

    # ─── Score Components ─────────────────────────────────────────────────

    def _calc_time_decay(self, conv):
        """
        Time decay — the core "too early = wrong" mechanism.

        Decay accelerates:
          - After half-life: 2.5x base rate
          - Final week before deadline: 5x base rate
          - After deadline: massive penalty
        """
        days = _days_elapsed(conv["set_date"])
        if days < 1:
            return 0  # Grace period on first day

        base_rate = self.config["base_decay_per_day"]
        half_life = conv.get("half_life_days", 30)
        deadline_str = conv.get("catalyst_deadline", "")

        # Base decay
        decay = base_rate

        # Accelerate after half-life
        if days > half_life:
            overshoot = (days - half_life) / half_life  # 0 at half-life, 1 at 2x
            decay *= (1 + overshoot * (self.config["accelerated_decay_mult"] - 1))

        # Massive acceleration near deadline
        if deadline_str:
            deadline = _parse_ts(deadline_str)
            now = datetime.now(timezone.utc)
            days_to_deadline = (deadline - now).total_seconds() / 86400

            if days_to_deadline < 0:
                # Past deadline — heavy penalty
                decay = base_rate * self.config["deadline_decay_mult"] * 2
            elif days_to_deadline < 7:
                # Final week — escalating urgency
                urgency = 1 - (days_to_deadline / 7)  # 0 at 7 days, 1 at deadline
                decay *= (1 + urgency * (self.config["deadline_decay_mult"] - 1))

        return -decay

    def _calc_price_signal(self, conv):
        """
        Price action — is the stock moving toward or away from the thesis?

        Moving toward target → small conviction boost
        Moving away from target → penalty
        Breaking new lows → bigger penalty
        """
        current = conv.get("current_price", 0)
        entry = conv.get("entry_price", 0)
        target = conv.get("target_price", 0)

        if not current or not entry:
            return 0

        pct_from_entry = (current - entry) / entry if entry else 0

        if target and target > entry:
            # Bullish thesis
            progress = (current - entry) / (target - entry) if (target - entry) else 0
            progress = max(-2, min(2, progress))  # Clamp extreme moves

            if progress > 0:
                # Moving toward target
                return self.config["price_confirm_boost"] * min(1, progress)
            else:
                # Moving away — scale penalty with magnitude
                penalty = self.config["price_deny_penalty"] * min(1, abs(progress))
                # Extra penalty for big drawdowns
                if pct_from_entry < -0.15:
                    penalty *= 1.5
                if pct_from_entry < -0.25:
                    penalty *= 2.0
                return penalty
        elif target and target < entry:
            # Bearish thesis (short conviction)
            if current < entry:
                return self.config["price_confirm_boost"]
            else:
                return self.config["price_deny_penalty"]

        return 0

    def _calc_volume_signal(self, symbol, conv):
        """
        Volume analysis — detect accumulation vs distribution.

        High volume + price up = accumulation → bullish
        High volume + price down = distribution → bearish
        Low volume + price movement = noise → neutral
        """
        lookback = self.config["volume_lookback_days"]
        bars = self._fetch_bars(symbol, lookback + 5)

        if bars is None or len(bars) < 5:
            return 0

        recent_vol = bars[-3:]   # Last 3 days
        avg_vol = bars[:-3]       # Rest for baseline

        if len(avg_vol) < 2:
            return 0

        recent_avg = np.mean([b.get("v", 0) for b in recent_vol]) if recent_vol else 0
        baseline_avg = np.mean([b.get("v", 0) for b in avg_vol]) if avg_vol else 1

        if baseline_avg == 0:
            return 0

        rel_volume = recent_avg / baseline_avg
        conv["volume_trend"] = round(rel_volume, 2)

        # Need significant volume to matter
        if rel_volume < 1.3:
            return 0  # Normal volume — no signal

        # Check if accumulation (up) or distribution (down)
        recent_prices = [b.get("c", 0) for b in recent_vol]
        if len(recent_prices) >= 2:
            price_direction = recent_prices[-1] - recent_prices[0]

            if price_direction > 0 and rel_volume > 1.5:
                return self.config["accumulation_boost"]
            elif price_direction < 0 and rel_volume > 1.5:
                return self.config["distribution_penalty"]

        return 0

    def _detect_storm(self, symbol):
        """
        Storm detection — is the whole market tanking, or just this stock?

        Returns storm_factor (0 to 1):
          0 = stock-specific move (no dampening)
          1 = pure market storm (full dampening)
        """
        proxy = self.config["market_proxy"]
        stock_bars = self._fetch_bars(symbol, 3)
        market_bars = self._fetch_bars(proxy, 3)

        if not stock_bars or not market_bars or len(stock_bars) < 2 or len(market_bars) < 2:
            return 0

        stock_ret = (stock_bars[-1]["c"] - stock_bars[-2]["c"]) / stock_bars[-2]["c"] if stock_bars[-2]["c"] else 0
        market_ret = (market_bars[-1]["c"] - market_bars[-2]["c"]) / market_bars[-2]["c"] if market_bars[-2]["c"] else 0

        # If market is also down significantly
        if market_ret < -0.005 and stock_ret < 0:
            # Both dropping — how correlated?
            # Simple ratio: if stock drops proportionally to market, it's a storm
            if abs(stock_ret) > 0 and abs(market_ret) > 0:
                ratio = min(1.0, abs(market_ret) / abs(stock_ret))
                return ratio if ratio > self.config["storm_correlation_threshold"] else ratio * 0.5

        return 0

    # ─── Phase Management ─────────────────────────────────────────────────

    def _update_phase(self, symbol, conv, portfolio_value=None, positions=None):
        """Update conviction phase based on current score and conditions."""
        score = conv["current_score"]
        old_phase = conv["phase"]
        cfg = self.config

        if old_phase in ("CLOSED", "EXITING"):
            return None  # Terminal states

        if old_phase == "EXPIRED":
            # Already expired — start exiting
            conv["phase"] = "EXITING"
            return {
                "type": "PHASE_CHANGE",
                "symbol": symbol,
                "from": "EXPIRED",
                "to": "EXITING",
                "reason": "Thesis expired, beginning systematic exit",
                "urgency": "HIGH"
            }

        # Score-based transitions
        new_phase = old_phase
        if score >= cfg["strong_threshold"]:
            new_phase = "ACCUMULATING"
        elif score >= cfg["moderate_threshold"]:
            new_phase = "HOLDING"
        elif score >= cfg["weakening_threshold"]:
            # Only transition to weakening from better states
            if old_phase in ("ACCUMULATING", "HOLDING"):
                new_phase = "HOLDING"  # Buffer zone — need confirmed weakness
            # Score below 50 for 2+ cycles → WEAKENING
            recent = conv.get("score_history", [])[-3:]
            if len(recent) >= 2 and all(h.get("score", 100) < cfg["moderate_threshold"] for h in recent):
                new_phase = "EXITING"  # Weakening → start reducing
        elif score >= cfg["fading_threshold"]:
            new_phase = "EXITING"
        elif score < cfg["auto_close_score"]:
            # Auto-close on very low conviction
            self.close_conviction(symbol, reason=f"Score dropped to {score:.0f}")
            return {
                "type": "AUTO_CLOSED",
                "symbol": symbol,
                "reason": f"Conviction score {score:.0f} below auto-close threshold {cfg['auto_close_score']}",
                "urgency": "CRITICAL"
            }

        if new_phase != old_phase:
            conv["phase"] = new_phase
            conv["score_history"].append({
                "ts": _now_ts(),
                "score": score,
                "reason": f"Phase: {old_phase} → {new_phase}"
            })
            return {
                "type": "PHASE_CHANGE",
                "symbol": symbol,
                "from": old_phase,
                "to": new_phase,
                "score": score,
                "reason": f"Score {score:.0f} triggered phase change",
                "urgency": "MEDIUM" if new_phase in ("ACCUMULATING", "HOLDING") else "HIGH"
            }

        return None

    # ─── Accumulation Logic ───────────────────────────────────────────────

    def _check_accumulation(self, symbol, conv, portfolio_value=None, positions=None):
        """
        Check if we should add to the position on a dip.

        Rules:
          - Only in ACCUMULATING phase (score >= 80)
          - Dip must exceed threshold (default -5% intraday)
          - Respect min_days_between_adds
          - Don't exceed max_position_pct
          - Don't exceed max_total_adds (% of original)
        """
        if not conv.get("accumulate_on_dips"):
            return None

        price = conv.get("current_price", 0)
        entry = conv.get("entry_price", 0)
        if not price or not entry:
            return None

        # Check dip threshold
        pct_from_entry = ((price - entry) / entry) * 100
        if pct_from_entry > conv.get("dip_threshold_pct", -5.0):
            return None  # Not dipped enough

        # Check days since last add
        if conv.get("last_add_date"):
            days_since = _days_elapsed(conv["last_add_date"])
            if days_since < conv.get("min_days_between_adds", 2):
                return None  # Too soon

        # Check max adds
        max_adds = entry * conv.get("max_position_pct", 40) / 100 * self.config["max_total_adds_pct"] / 100
        if conv.get("total_added", 0) >= max_adds and max_adds > 0:
            return None  # Already added enough

        # Check portfolio concentration
        if portfolio_value and positions:
            current_pos_value = 0
            for pos in positions if isinstance(positions, list) else []:
                if pos.get("symbol", "").upper() == symbol:
                    current_pos_value = float(pos.get("market_value", 0))
            current_pct = (current_pos_value / portfolio_value * 100) if portfolio_value else 0
            if current_pct >= conv.get("max_position_pct", 40):
                return None  # At max allocation

        add_amount = conv.get("max_add_per_dip") or self.config["default_add_amount"]

        return {
            "type": "ACCUMULATE",
            "symbol": symbol,
            "amount": add_amount,
            "price": price,
            "pct_from_entry": round(pct_from_entry, 2),
            "conviction_score": conv["current_score"],
            "reason": f"Dip buy: {pct_from_entry:+.1f}% from entry, conviction {conv['current_score']:.0f}",
            "urgency": "MEDIUM"
        }

    # ─── External Sentiment Integration ───────────────────────────────────

    def ingest_sentiment_event(self, symbol, headline, sentiment_score,
                                source="news", event_type="headline"):
        """
        Feed a sentiment event into the conviction system.

        Args:
            symbol: Ticker
            headline: News headline or event description
            sentiment_score: -1.0 (very bearish) to +1.0 (very bullish)
            source: news|earnings|analyst|insider|social
            event_type: headline|earnings_beat|earnings_miss|upgrade|downgrade|insider_buy|insider_sell
        """
        symbol = symbol.upper()
        if symbol not in self.convictions:
            return

        conv = self.convictions[symbol]
        weight = self.config["sentiment_weight"]

        # Event type multipliers
        type_multipliers = {
            "earnings_beat": self.config["earnings_beat_boost"] / weight,
            "earnings_miss": abs(self.config["earnings_miss_penalty"]) / weight,
            "upgrade": 1.5,
            "downgrade": 1.5,
            "insider_buy": 1.3,
            "insider_sell": 1.8,  # Insider selling is more informative
            "acquisition_rumor": 2.0,
            "acquisition_denied": 2.5,
            "headline": 1.0,
        }
        multiplier = type_multipliers.get(event_type, 1.0)

        # Calculate impact
        impact = sentiment_score * weight * multiplier

        # Cap single-event impact
        impact = max(-25, min(25, impact))

        # Update EMA trend
        alpha = self.config["sentiment_ema_alpha"]
        old_trend = conv.get("sentiment_trend", 0)
        conv["sentiment_trend"] = round(old_trend * (1 - alpha) + sentiment_score * alpha, 3)

        # Apply to score
        old_score = conv["current_score"]
        conv["current_score"] = round(max(0, min(100, old_score + impact)), 1)

        # Record event
        event = {
            "ts": _now_ts(),
            "headline": headline[:200],
            "sentiment": round(sentiment_score, 3),
            "source": source,
            "type": event_type,
            "impact": round(impact, 1)
        }
        if "news_events" not in conv:
            conv["news_events"] = []
        conv["news_events"].append(event)
        # Keep last 50 events
        conv["news_events"] = conv["news_events"][-50:]

        conv["score_history"].append({
            "ts": _now_ts(),
            "score": conv["current_score"],
            "reason": f"Sentiment {event_type}: {sentiment_score:+.2f} → impact {impact:+.1f} ({headline[:60]})"
        })

        conv["updated_at"] = _now_ts()
        self._save()

        logger.info(f"Sentiment {symbol}: {event_type} {sentiment_score:+.2f} → "
                    f"score {old_score:.0f} → {conv['current_score']:.0f} ({headline[:50]})")

    def ingest_earnings(self, symbol, beat_or_miss, eps_surprise_pct=0,
                         revenue_surprise_pct=0, guidance="neutral"):
        """
        Process earnings event — one of the biggest conviction movers.

        Args:
            symbol: Ticker
            beat_or_miss: "beat" | "miss" | "inline"
            eps_surprise_pct: EPS surprise % (positive = beat)
            revenue_surprise_pct: Revenue surprise %
            guidance: "raised" | "lowered" | "maintained" | "neutral"
        """
        symbol = symbol.upper()
        if symbol not in self.convictions:
            return

        # Composite earnings sentiment
        if beat_or_miss == "beat":
            base = 0.6
            event_type = "earnings_beat"
        elif beat_or_miss == "miss":
            base = -0.7
            event_type = "earnings_miss"
        else:
            base = 0.0
            event_type = "headline"

        # Surprise magnitude matters
        surprise_factor = (abs(eps_surprise_pct) + abs(revenue_surprise_pct)) / 20  # Normalize
        surprise_factor = min(1.0, surprise_factor)
        base *= (1 + surprise_factor)

        # Guidance is forward-looking — weighs heavily
        guidance_adj = {
            "raised": 0.3,
            "maintained": 0.0,
            "lowered": -0.4,
            "neutral": 0.0,
        }.get(guidance, 0)

        sentiment = max(-1, min(1, base + guidance_adj))

        headline = (f"Earnings {beat_or_miss}: EPS {eps_surprise_pct:+.1f}%, "
                   f"Rev {revenue_surprise_pct:+.1f}%, Guidance {guidance}")

        self.ingest_sentiment_event(symbol, headline, sentiment,
                                     source="earnings", event_type=event_type)

    def ingest_catalyst_update(self, symbol, status, details=""):
        """
        Update catalyst status — the most important signal.

        Args:
            symbol: Ticker
            status: "confirmed" | "progressing" | "uncertain" | "denied" | "delayed"
            details: Description of update
        """
        symbol = symbol.upper()
        if symbol not in self.convictions:
            return

        conv = self.convictions[symbol]
        impact_map = {
            "confirmed": 30,       # Thesis confirmed — max boost
            "progressing": 12,     # Moving in right direction
            "uncertain": -5,       # Muddied waters
            "denied": -35,         # Thesis denied — major penalty
            "delayed": -10,        # Pushed back — time cost
        }
        impact = impact_map.get(status, 0)

        old_score = conv["current_score"]
        conv["current_score"] = round(max(0, min(100, old_score + impact)), 1)

        if status == "confirmed":
            conv["phase"] = "TRIGGERED"

        if status == "denied":
            conv["phase"] = "EXITING"

        conv["score_history"].append({
            "ts": _now_ts(),
            "score": conv["current_score"],
            "reason": f"Catalyst {status}: {details[:100]} (impact {impact:+d})"
        })

        conv["updated_at"] = _now_ts()
        self._save()

        logger.info(f"Catalyst update {symbol}: {status} → "
                    f"score {old_score:.0f} → {conv['current_score']:.0f}")

    # ─── Risk Fortress Integration ────────────────────────────────────────

    def get_risk_overrides(self, symbol):
        """
        Get risk parameter overrides for a conviction position.
        Returns None if no conviction, or dict of overrides.
        """
        symbol = symbol.upper()
        conv = self.convictions.get(symbol)
        if not conv:
            return None

        score = conv["current_score"]
        phase = conv["phase"]

        # Graduated overrides based on conviction strength
        overrides = {
            "is_conviction": True,
            "symbol": symbol,
            "conviction_score": score,
            "phase": phase,
            "thesis": conv["thesis"],
        }

        if phase in ("CLOSED", "EXITING"):
            # No overrides when exiting — normal rules apply
            overrides["is_conviction"] = False
            return overrides

        if score >= self.config["strong_threshold"]:
            # Strong conviction — wide latitude
            overrides.update({
                "max_position_pct": conv.get("max_position_pct", 40),
                "override_zombie_kill": True,
                "override_stop_loss": True,
                "stop_loss_pct": conv.get("stop_loss_pct", -25),
                "allow_accumulation": True,
            })
        elif score >= self.config["moderate_threshold"]:
            # Moderate — hold but don't add
            overrides.update({
                "max_position_pct": min(conv.get("max_position_pct", 40), 30),
                "override_zombie_kill": True,
                "override_stop_loss": True,
                "stop_loss_pct": max(conv.get("stop_loss_pct", -25), -20),  # Tighter
                "allow_accumulation": False,
            })
        elif score >= self.config["weakening_threshold"]:
            # Weakening — tighten everything
            overrides.update({
                "max_position_pct": 25,
                "override_zombie_kill": False,  # Zombie kill can proceed
                "override_stop_loss": True,
                "stop_loss_pct": -15,  # Much tighter
                "allow_accumulation": False,
            })
        else:
            # Fading/dead — no overrides
            overrides["is_conviction"] = False

        return overrides

    # ─── Orchestrator Integration ─────────────────────────────────────────

    def should_skip_exit(self, symbol):
        """
        Should the orchestrator skip an exit signal for this symbol?
        (Because conviction says hold)
        """
        overrides = self.get_risk_overrides(symbol)
        if not overrides or not overrides.get("is_conviction"):
            return False

        conv = self.convictions.get(symbol.upper())
        if not conv:
            return False

        # Don't skip exit if price hit max pain
        price = conv.get("current_price", 0)
        max_pain = conv.get("max_pain_price", 0)
        if price and max_pain and price <= max_pain:
            return False

        # Skip exit if conviction is strong enough
        return conv["current_score"] >= self.config["moderate_threshold"]

    def should_allow_buy(self, symbol, portfolio_value=None, current_position_pct=0):
        """
        Should the orchestrator allow a buy for this symbol?
        (Conviction positions may have different buy limits)
        """
        overrides = self.get_risk_overrides(symbol)
        if not overrides or not overrides.get("is_conviction"):
            return True  # Not a conviction stock — use normal rules

        max_pct = overrides.get("max_position_pct", 40)
        if current_position_pct >= max_pct:
            return False

        return overrides.get("allow_accumulation", False)

    # ─── Data Fetching ────────────────────────────────────────────────────

    def _fetch_current_price(self, symbol):
        """Get latest price from Alpaca."""
        data = _api_get(f"{ALPACA_DATA}/v2/stocks/{symbol}/quotes/latest",
                       {"feed": "iex"})
        if data and "quote" in data:
            q = data["quote"]
            return float(q.get("ap", 0) or q.get("bp", 0))
        # Fallback to last bar
        bars = self._fetch_bars(symbol, 2)
        if bars:
            return bars[-1].get("c", 0)
        return None

    def _fetch_bars(self, symbol, days=20):
        """Fetch daily bars from Alpaca."""
        end = datetime.utcnow()
        start = end - timedelta(days=days + 5)
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT00:00:00Z"),
            "feed": "iex",
            "limit": days,
        }
        data = _api_get(f"{ALPACA_DATA}/v2/stocks/{symbol}/bars", params)
        if data and "bars" in data:
            return data["bars"]
        return None

    # ─── Status & Reporting ───────────────────────────────────────────────

    def status_report(self):
        """Generate a human-readable status report."""
        if not self.convictions:
            return "No active convictions."

        lines = ["═══ CONVICTION POSITIONS ═══\n"]

        for symbol, conv in self.convictions.items():
            score = conv["current_score"]
            phase = conv["phase"]
            entry = conv.get("entry_price", 0)
            current = conv.get("current_price", 0)
            target = conv.get("target_price", 0)
            max_pain = conv.get("max_pain_price", 0)
            days = _days_elapsed(conv["set_date"])

            # Score bar
            bar_len = 20
            filled = int(score / 100 * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)

            # PnL
            pnl_pct = ((current - entry) / entry * 100) if entry else 0

            # Sentiment trend arrow
            trend = conv.get("sentiment_trend", 0)
            trend_arrow = "↑" if trend > 0.1 else "↓" if trend < -0.1 else "→"

            lines.append(f"  {symbol} [{phase}]")
            lines.append(f"  Score: [{bar}] {score:.0f}/100")
            lines.append(f"  Thesis: {conv['thesis']}")
            lines.append(f"  Price: ${current:.2f} (entry ${entry:.2f}, {pnl_pct:+.1f}%)")
            lines.append(f"  Target: ${target:.2f} | Max Pain: ${max_pain:.2f}")
            lines.append(f"  Held: {days:.0f} days | Sentiment: {trend_arrow} ({trend:+.2f})")
            lines.append(f"  Volume: {conv.get('volume_trend', 0):.1f}x avg")

            # Recent events
            events = conv.get("news_events", [])[-3:]
            if events:
                lines.append(f"  Recent events:")
                for e in events:
                    lines.append(f"    {e.get('type', '?')}: {e.get('headline', '')[:50]} "
                               f"({e.get('sentiment', 0):+.2f})")

            # Deadline
            if conv.get("catalyst_deadline"):
                deadline = _parse_ts(conv["catalyst_deadline"])
                remaining = (deadline - datetime.now(timezone.utc)).days
                if remaining > 0:
                    lines.append(f"  Deadline: {remaining} days remaining")
                else:
                    lines.append(f"  ⚠️ DEADLINE PASSED ({abs(remaining)} days ago)")

            lines.append("")

        return "\n".join(lines)

    def get_summary(self):
        """Compact summary for dashboard/Telegram."""
        summaries = []
        for symbol, conv in self.convictions.items():
            score = conv["current_score"]
            phase = conv["phase"]
            emoji = "🟢" if score >= 80 else "🟡" if score >= 60 else "🟠" if score >= 40 else "🔴"
            summaries.append(f"{emoji} {symbol}: {score:.0f}/100 [{phase}]")
        return summaries


# ─── Convenience Functions ────────────────────────────────────────────────────

def load_conviction_manager(config=None):
    """Factory function to create and load a ConvictionManager."""
    return ConvictionManager(config)


# ─── Test / Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    cm = ConvictionManager()

    # Set GME conviction
    print("Setting GME conviction...\n")
    cm.set_conviction(
        symbol="GME",
        thesis="GameStop acquisition by major tech company",
        catalyst="Acquisition announcement or confirmed bid",
        catalyst_deadline="2026-06-30T00:00:00Z",
        target_price=45.0,
        max_pain_price=12.0,
        base_score=78,
        catalyst_type="event",
        max_position_pct=45.0,
        notes="Strong rumors, Ryan Cohen has been making moves. Hold through volatility."
    )

    # Simulate some events
    print("Simulating sentiment events...\n")
    cm.ingest_sentiment_event(
        "GME", "GameStop reportedly in talks with major tech firm",
        sentiment_score=0.8, source="news", event_type="acquisition_rumor"
    )

    cm.ingest_sentiment_event(
        "GME", "Meme stocks slide as market sells off broadly",
        sentiment_score=-0.3, source="news", event_type="headline"
    )

    cm.ingest_earnings(
        "GME", "beat", eps_surprise_pct=15.0,
        revenue_surprise_pct=8.0, guidance="maintained"
    )

    # Print status
    print(cm.status_report())

    # Check risk overrides
    overrides = cm.get_risk_overrides("GME")
    print(f"\nRisk overrides: {json.dumps(overrides, indent=2)}")

    # Check if exit should be skipped
    print(f"\nSkip exit for GME? {cm.should_skip_exit('GME')}")
