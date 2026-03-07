"""
Trade Tracker — Records every trade with its signal context and outcome.

Pulls from Alpaca order/activity history, matches entries to exits,
and stores the full context needed for the adaptive engine to learn.

Designed for Raspberry Pi: pure Python + requests, no heavy deps.
"""

import json
import os
import time
import logging
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("adaptive.tracker")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
ALPACA_BASE = os.getenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID", "")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY", "")

TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), "trade_log.json")


class TradeTracker:
    """
    Tracks trades from Alpaca, records signal context at entry,
    and computes outcomes when positions close.
    """

    def __init__(self, config=None, trade_log_path=None):
        self.config = config or {}
        self.trade_log_path = trade_log_path or TRADE_LOG_FILE
        self.trades = self._load_trades()
        self._headers = {
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_trades(self):
        if os.path.exists(self.trade_log_path):
            try:
                with open(self.trade_log_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load trade log: {e}")
        return {"open": {}, "closed": [], "metadata": {"total_trades": 0}}

    def _save_trades(self):
        try:
            tmp = self.trade_log_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.trades, f, indent=2, default=str)
            os.replace(tmp, self.trade_log_path)
        except Exception as e:
            logger.error(f"Failed to save trade log: {e}")

    # ------------------------------------------------------------------
    # Alpaca API helpers
    # ------------------------------------------------------------------
    def _api_get(self, endpoint, params=None):
        if not requests:
            logger.error("requests library not available")
            return None
        url = f"{ALPACA_BASE}{endpoint}"
        try:
            r = requests.get(url, headers=self._headers, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Alpaca API error ({endpoint}): {e}")
            return None

    def get_current_positions(self):
        """Fetch all current open positions from Alpaca."""
        return self._api_get("/v2/positions") or []

    def get_closed_orders(self, after=None, limit=100):
        """Fetch recently filled orders."""
        params = {"status": "filled", "limit": limit, "direction": "desc"}
        if after:
            params["after"] = after
        return self._api_get("/v2/orders", params) or []

    def get_account_activities(self, activity_type="FILL", after=None):
        """Fetch trade fill activities."""
        params = {"activity_type": activity_type, "direction": "desc"}
        if after:
            params["after"] = after
        return self._api_get(f"/v2/account/activities/{activity_type}", params) or []

    # ------------------------------------------------------------------
    # Trade recording
    # ------------------------------------------------------------------
    def record_entry(self, symbol, entry_price, qty, side, signals, sector="other",
                     strategy="adaptive", metadata=None):
        """
        Record a new trade entry with its signal context.

        signals: dict of signal values at entry time, e.g.:
            {
                "rsi": {"value": 35, "signal": "buy", "strength": 0.7},
                "macd": {"value": 0.5, "signal": "buy", "strength": 0.6},
                "sentiment": {"value": 0.8, "signal": "buy", "strength": 0.8},
                "volume": {"value": 1.5, "signal": "buy", "strength": 0.5},
                "adx": {"value": 28, "signal": "trending", "strength": 0.6},
                "sma_cross": {"value": 1, "signal": "buy", "strength": 0.4}
            }
        """
        trade_id = f"{symbol}_{int(time.time())}"
        now = datetime.utcnow()

        # Determine time bucket
        hour = now.hour
        if hour < 9:
            time_bucket = "pre_market"
        elif hour < 11:
            time_bucket = "morning"
        elif hour < 14:
            time_bucket = "midday"
        else:
            time_bucket = "afternoon"

        entry = {
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "qty": float(qty),
            "entry_price": float(entry_price),
            "entry_time": now.isoformat() + "Z",
            "entry_day_of_week": now.strftime("%A"),
            "time_bucket": time_bucket,
            "signals": signals,
            "sector": sector,
            "strategy": strategy,
            "metadata": metadata or {},
            "status": "open"
        }

        self.trades["open"][symbol] = entry
        self.trades["metadata"]["total_trades"] = (
            self.trades["metadata"].get("total_trades", 0) + 1
        )
        self._save_trades()
        logger.info(f"Recorded entry: {symbol} @ ${entry_price} ({side})")
        return trade_id

    def record_exit(self, symbol, exit_price, exit_reason="signal"):
        """
        Record a position exit and compute the outcome.

        exit_reason: "signal", "stop_loss", "trailing_stop", "take_profit",
                     "manual", "eod" (end of day)
        """
        if symbol not in self.trades["open"]:
            logger.warning(f"No open trade found for {symbol}")
            return None

        entry = self.trades["open"].pop(symbol)
        now = datetime.utcnow()

        entry_price = entry["entry_price"]
        ep = float(exit_price)
        qty = entry["qty"]
        side = entry["side"]

        # PnL calculation
        if side == "buy":
            pnl_per_share = ep - entry_price
        else:
            pnl_per_share = entry_price - ep

        pnl_total = pnl_per_share * qty
        pnl_pct = (pnl_per_share / entry_price) * 100 if entry_price > 0 else 0

        # Hold duration
        try:
            entry_dt = datetime.fromisoformat(entry["entry_time"].rstrip("Z"))
            hold_seconds = (now - entry_dt).total_seconds()
            hold_hours = hold_seconds / 3600
        except Exception:
            hold_hours = 0

        # Determine if each signal was "correct" (profitable direction)
        signal_outcomes = {}
        win = pnl_total > 0
        for sig_name, sig_data in entry.get("signals", {}).items():
            if isinstance(sig_data, dict):
                signal_correct = win  # Signal was correct if trade was profitable
                signal_outcomes[sig_name] = {
                    "entry_value": sig_data.get("value"),
                    "entry_signal": sig_data.get("signal"),
                    "entry_strength": sig_data.get("strength", 0.5),
                    "correct": signal_correct,
                    "pnl_contribution": pnl_pct * sig_data.get("strength", 0.5)
                }

        closed_trade = {
            **entry,
            "exit_price": ep,
            "exit_time": now.isoformat() + "Z",
            "exit_reason": exit_reason,
            "pnl_total": round(pnl_total, 4),
            "pnl_pct": round(pnl_pct, 4),
            "hold_hours": round(hold_hours, 2),
            "win": win,
            "signal_outcomes": signal_outcomes,
            "status": "closed"
        }

        self.trades["closed"].append(closed_trade)

        # Keep only last N closed trades to prevent unbounded growth
        max_closed = self.config.get("learning", {}).get("lookback_trades", 200)
        if len(self.trades["closed"]) > max_closed * 2:
            self.trades["closed"] = self.trades["closed"][-max_closed:]

        self._save_trades()
        logger.info(
            f"Recorded exit: {symbol} @ ${ep} | "
            f"PnL: ${pnl_total:+.2f} ({pnl_pct:+.2f}%) | "
            f"Hold: {hold_hours:.1f}h | Reason: {exit_reason}"
        )
        return closed_trade

    # ------------------------------------------------------------------
    # Sync with Alpaca (detect exits we didn't explicitly record)
    # ------------------------------------------------------------------
    def sync_with_alpaca(self):
        """
        Compare our open trades with Alpaca positions.
        If a position disappeared, it was closed — record the exit.
        """
        positions = self.get_current_positions()
        if positions is None:
            return []

        current_symbols = {p["symbol"] for p in positions}
        our_open = set(self.trades["open"].keys())
        newly_closed = []

        for symbol in our_open - current_symbols:
            # Position no longer exists — try to find exit price from orders
            orders = self.get_closed_orders(limit=20)
            exit_price = None
            exit_reason = "unknown"

            if orders:
                for order in orders:
                    if (order.get("symbol") == symbol and
                            order.get("side") == "sell" and
                            order.get("status") == "filled"):
                        exit_price = float(order.get("filled_avg_price", 0))
                        # Infer exit reason from order type
                        otype = order.get("type", "")
                        if "stop" in otype:
                            exit_reason = "stop_loss"
                        elif "trail" in otype.lower() or "trailing" in str(
                                order.get("trail_percent", "")):
                            exit_reason = "trailing_stop"
                        elif "limit" in otype:
                            exit_reason = "take_profit"
                        else:
                            exit_reason = "signal"
                        break

            if exit_price and exit_price > 0:
                result = self.record_exit(symbol, exit_price, exit_reason)
                if result:
                    newly_closed.append(result)
            else:
                # Can't determine exit price — record with last known price
                logger.warning(f"Could not find exit price for {symbol}, skipping")

        # Also check for new positions we don't know about
        for pos in positions:
            sym = pos["symbol"]
            if sym not in self.trades["open"]:
                # Position exists but we didn't record entry — backfill
                logger.info(f"Found untracked position: {sym}, backfilling")
                self.record_entry(
                    symbol=sym,
                    entry_price=float(pos.get("avg_entry_price", 0)),
                    qty=float(pos.get("qty", 0)),
                    side="buy" if pos.get("side") == "long" else "sell",
                    signals={},  # No signal context for backfilled trades
                    sector="other",
                    strategy="existing",
                    metadata={"backfilled": True,
                              "unrealized_pnl": float(pos.get("unrealized_pl", 0))}
                )

        return newly_closed

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def get_recent_trades(self, n=50):
        """Get the N most recent closed trades."""
        return self.trades["closed"][-n:]

    def get_signal_stats(self, lookback=50):
        """
        Compute per-signal accuracy over recent trades.
        Returns: {signal_name: {"wins": N, "losses": N, "accuracy": float}}
        """
        recent = self.get_recent_trades(lookback)
        stats = {}

        for trade in recent:
            for sig_name, outcome in trade.get("signal_outcomes", {}).items():
                if sig_name not in stats:
                    stats[sig_name] = {"wins": 0, "losses": 0, "total_pnl": 0}

                if outcome.get("correct"):
                    stats[sig_name]["wins"] += 1
                else:
                    stats[sig_name]["losses"] += 1

                stats[sig_name]["total_pnl"] += outcome.get("pnl_contribution", 0)

        for sig_name in stats:
            total = stats[sig_name]["wins"] + stats[sig_name]["losses"]
            stats[sig_name]["accuracy"] = (
                stats[sig_name]["wins"] / total if total > 0 else 0.5
            )
            stats[sig_name]["total"] = total

        return stats

    def get_sector_stats(self, lookback=50):
        """Per-sector win rate."""
        recent = self.get_recent_trades(lookback)
        stats = {}
        for trade in recent:
            sector = trade.get("sector", "other")
            if sector not in stats:
                stats[sector] = {"wins": 0, "losses": 0, "total_pnl": 0}
            if trade.get("win"):
                stats[sector]["wins"] += 1
            else:
                stats[sector]["losses"] += 1
            stats[sector]["total_pnl"] += trade.get("pnl_pct", 0)

        for sector in stats:
            total = stats[sector]["wins"] + stats[sector]["losses"]
            stats[sector]["win_rate"] = (
                stats[sector]["wins"] / total if total > 0 else 0.5
            )
        return stats

    def get_time_bucket_stats(self, lookback=50):
        """Per-time-bucket win rate."""
        recent = self.get_recent_trades(lookback)
        stats = {}
        for trade in recent:
            bucket = trade.get("time_bucket", "unknown")
            if bucket not in stats:
                stats[bucket] = {"wins": 0, "losses": 0, "total_pnl": 0}
            if trade.get("win"):
                stats[bucket]["wins"] += 1
            else:
                stats[bucket]["losses"] += 1
            stats[bucket]["total_pnl"] += trade.get("pnl_pct", 0)

        for bucket in stats:
            total = stats[bucket]["wins"] + stats[bucket]["losses"]
            stats[bucket]["win_rate"] = (
                stats[bucket]["wins"] / total if total > 0 else 0.5
            )
        return stats

    def get_hold_duration_stats(self, lookback=50):
        """Analyze relationship between hold duration and outcomes."""
        recent = self.get_recent_trades(lookback)
        buckets = {
            "scalp_0_1h": {"range": (0, 1), "wins": 0, "losses": 0, "pnl": 0},
            "short_1_4h": {"range": (1, 4), "wins": 0, "losses": 0, "pnl": 0},
            "intraday_4_8h": {"range": (4, 8), "wins": 0, "losses": 0, "pnl": 0},
            "swing_8h_plus": {"range": (8, 999), "wins": 0, "losses": 0, "pnl": 0},
        }

        for trade in recent:
            hours = trade.get("hold_hours", 0)
            for bname, bdata in buckets.items():
                lo, hi = bdata["range"]
                if lo <= hours < hi:
                    if trade.get("win"):
                        bdata["wins"] += 1
                    else:
                        bdata["losses"] += 1
                    bdata["pnl"] += trade.get("pnl_pct", 0)
                    break

        for bname in buckets:
            total = buckets[bname]["wins"] + buckets[bname]["losses"]
            buckets[bname]["win_rate"] = (
                buckets[bname]["wins"] / total if total > 0 else 0.5
            )
            buckets[bname]["total"] = total
            del buckets[bname]["range"]

        return buckets

    def summary(self):
        """Quick summary of trade tracker state."""
        return {
            "open_positions": len(self.trades["open"]),
            "closed_trades": len(self.trades["closed"]),
            "total_trades": self.trades["metadata"].get("total_trades", 0),
            "signal_stats": self.get_signal_stats(),
            "sector_stats": self.get_sector_stats(),
            "time_stats": self.get_time_bucket_stats(),
        }
