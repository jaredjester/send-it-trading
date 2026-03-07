#!/usr/bin/env python3
"""
Options Trader — Options-first execution for Strategy V2 orchestrator.

Philosophy:
  Every candidate gets checked for options first. If a liquid, affordable
  contract exists → trade options (leverage + defined risk). Otherwise
  fall back to stock buy.

Contract selection:
  - Direction: bullish signal types → CALL; bearish → PUT
  - Expiration: 14–35 days out (enough time, not too much premium)
  - Strike: ATM or 1 strike OTM (best balance of premium vs probability)
  - Liquidity filter: open_interest >= int(_cfg('options.min_open_interest', 10)) (avoid illiquid contracts)
  - Affordability filter: premium × 100 <= max_budget_per_contract

Position management:
  - Always 1 contract (100 shares) to start
  - Stop loss: exit if position P&L < -50% of premium paid
  - Take profit: exit at +100% (double)
  - Expiry guard: close any contract with < 3 days to expiration

Fallback to stock: when
  - No active contracts found in expiry window
  - All contracts illiquid (OI < int(_cfg('options.min_open_interest', 10)))
  - Cheapest premium × 100 > max_budget_per_contract
  - API errors

Usage (from orchestrator):
    from options_trader import OptionsTrader
    trader = OptionsTrader(api_key, api_secret)
    result = trader.execute_options_buy(symbol, signal_type, budget)
    if result["traded"]:
        # options order placed
    else:
        # fall back to stock
"""

import logging
import uuid
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("options_trader")

from core.dynamic_config import cfg as _cfg
from core.trading_db import db

PLANS_FILE = Path(__file__).parent.parent / "state" / "options_plans.jsonl"

def _write_plan(plan: dict):
    """Record a plan entry in database. Maps options plan schema → DB positions schema."""
    try:
        db.record_position({
            "id":            plan["id"],
            "symbol":        plan["symbol"],
            "qty":           float(plan.get("contracts", 1)),
            "market_value":  plan.get("entry_notional"),
            "unrealized_pl": 0.0,
            "entry_price":   plan.get("entry_price"),
            "stop_price":    plan.get("stop_price"),
            "target_price":  plan.get("target_price"),
            "timestamp":     plan.get("entry_ts"),
            # Store full plan as JSON for dashboard recovery
            "_raw": plan,
        })
    except Exception as e:
        logger.warning(f"Plan write failed: {e}")

def _update_plan(plan_id: str, updates: dict):
    """Update an existing plan entry by plan_id."""
    try:
        db.update_position(plan_id, updates)
    except Exception as e:
        logger.warning(f"Plan update failed: {e}")


# ─── Config ───────────────────────────────────────────────────────────────────

BASE_URL        = "https://api.alpaca.markets"
DATA_URL        = "https://data.alpaca.markets"

# All options parameters loaded from cfg() → live_config.json
# Defaults: options.max_premium=1.50, options.min_open_interest=10,
#           options.min_expiry_days=14, options.max_expiry_days=35,
#           options.stop_loss_pct=0.50, options.take_profit_pct=1.00,
#           options.expiry_guard_days=3

# Signal type → option direction
BULLISH_SIGNALS = {
    "finviz_momentum", "finviz_breakout", "finviz_postearnings",
    "finviz_preearnings", "finviz_relstrength", "finviz_insider",
    "finviz_oversold", "gap", "catalyst", "watchlist",
}
BEARISH_SIGNALS = {"finviz_bearish", "short"}


# ─── Core class ───────────────────────────────────────────────────────────────

class OptionsTrader:
    """
    Options-first execution engine. Plugs into the orchestrator's execute_buy flow.

    On every buy signal:
      1. Determine direction (call/put) from signal type
      2. Query Alpaca options chain for affordable, liquid contracts
      3. Select best contract (highest OI within budget)
      4. Place market order
      5. Return result dict — orchestrator falls back to stock if traded=False
    """

    def __init__(self, api_key: str, api_secret: str):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.headers    = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def execute_options_buy(
        self,
        symbol: str,
        signal_type: str,
        budget: float,
        score: float = 0.0,
    ) -> dict:
        """
        Try to buy an options contract for this symbol.

        Returns:
            {
                "traded":   bool,       # True = options order placed
                "reason":   str,        # Why it did/didn't trade
                "contract": str | None, # Option symbol placed
                "premium":  float,      # Per-share premium
                "notional": float,      # Total cost (premium × 100)
                "direction": str,       # "call" or "put"
            }
        """
        direction = self._get_direction(signal_type)

        # Check account options buying power
        options_bp = self._get_options_buying_power()
        if options_bp is None:
            return self._no_trade("Could not fetch options buying power")
        if options_bp < 10:
            return self._no_trade(f"Insufficient options BP: ${options_bp:.2f}")

        max_per_contract = min(budget, options_bp, _cfg('options.max_premium', 1.50) * 100)

        # Find best contract
        contract = self._find_best_contract(symbol, direction, max_per_contract)
        if not contract:
            return self._no_trade(
                f"No affordable liquid {direction} contract found for {symbol} "
                f"(budget=${max_per_contract:.0f}, min_OI={int(_cfg('options.min_open_interest', 10))}, "
                f"window={int(_cfg('options.min_expiry_days', 14))}-{int(_cfg('options.max_expiry_days', 35))}d)"
            )

        contract_symbol = contract["symbol"]
        premium         = float(contract.get("close_price") or contract.get("last_price", 0))
        notional        = premium * 100  # 1 contract = 100 shares

        if notional > max_per_contract:
            return self._no_trade(
                f"{contract_symbol} premium ${notional:.2f} exceeds budget ${max_per_contract:.0f}"
            )

        # Place order
        success, order_id = self._place_options_order(contract_symbol, qty=1)
        if not success:
            return self._no_trade(f"Order failed for {contract_symbol}")

        plan_id = str(uuid.uuid4())
        try:
            from datetime import datetime as _dt, timezone as _tz
            decoded = self._decode_option_symbol_local(contract_symbol)
            expiry_str = decoded.get("expiry", "") if decoded else ""
            stop_price   = round(premium * _cfg('options.stop_loss_pct', 0.50) * -1, 4)   # premium × 0.50 → exit price
            target_price = round(premium * (1 + _cfg('options.take_profit_pct', 1.00)), 4) # premium × 2.0
            stop_dollar   = round(stop_price * 100, 2)
            target_dollar = round(target_price * 100, 2)
            plan = {
                "id":              plan_id,
                "plan_id":         plan_id,
                "symbol":          symbol,
                "occ_symbol":      contract_symbol,
                "direction":       direction,
                "contracts":       1,
                "entry_price":     premium,
                "stop_price":      stop_price,
                "target_price":    target_price,
                "max_loss_dollars": stop_dollar,
                "target_gain_dollars": target_dollar,
                "risk_reward":     round(_cfg('options.take_profit_pct', 1.00) / abs(_cfg('options.stop_loss_pct', 0.50)), 1),
                "target_date":     expiry_str + "T16:00:00+00:00" if expiry_str else None,
                "entry_ts":        _dt.now(_tz.utc).isoformat(),
                "status":          "open",
                "strategy":        "options_v2",
                "signal_type":     signal_type,
                "alpha_score":     score,
                "ev_at_entry":     round(score, 1),
                "entry_notional":  notional,
                "thesis":          (
                    f"{symbol} {direction.upper()} | signal={signal_type} | "
                    f"score={score:.1f} | strike=${decoded.get('strike', 0):.2f} "
                    f"| exp {expiry_str[5:] if expiry_str else '?'} | "
                    f"entry=${premium:.2f} stop=${stop_price:.2f} target=${target_price:.2f}"
                ),
            }
            _write_plan(plan)
            logger.info(f"Plan written: {plan_id} ({symbol} {direction.upper()} exp {expiry_str})")
        except Exception as _pe:
            logger.warning(f"Plan write failed: {_pe}")
            plan_id = None

        logger.info(
            f"\u2705 OPTIONS BUY: {contract_symbol} | {direction.upper()} | "
            f"premium=${premium:.2f}/share | notional=${notional:.2f} | "
            f"score={score:.1f} | signal={signal_type}"
        )
        return {
            "traded":    True,
            "reason":    "options order placed",
            "contract":  contract_symbol,
            "premium":   premium,
            "notional":  notional,
            "direction": direction,
            "order_id":  order_id,
            "plan_id":   plan_id,
        }

    def get_options_positions(self) -> list:
        """
        Return all currently held option positions.
        Option positions have symbols like ANAB260320C00035000.
        """
        try:
            r = requests.get(
                f"{BASE_URL}/v2/positions",
                headers=self.headers,
                timeout=10,
            )
            r.raise_for_status()
            positions = r.json()
            return [p for p in positions if self._is_option_symbol(p.get("symbol", ""))]
        except Exception as e:
            logger.warning(f"get_options_positions failed: {e}")
            return []

    def manage_options_positions(self) -> list:
        """
        Check open options positions and close any that hit stop/profit/expiry.
        Returns list of closed contract symbols.
        """
        closed = []
        positions = self.get_options_positions()

        for pos in positions:
            symbol    = pos.get("symbol", "")
            qty       = float(pos.get("qty", 0))
            unrealized_plpc = float(pos.get("unrealized_plpc", 0))  # decimal, e.g. -0.5 = -50%
            expiry    = self._parse_expiry_from_symbol(symbol)

            reason = None

            if unrealized_plpc <= -_cfg('options.stop_loss_pct', 0.50):
                reason = f"stop loss hit ({unrealized_plpc:.0%})"
            elif unrealized_plpc >= _cfg('options.take_profit_pct', 1.00):
                reason = f"take profit hit ({unrealized_plpc:.0%})"
            elif expiry and (expiry - datetime.now().date()).days <= int(_cfg('options.expiry_guard_days', 3)):
                reason = f"expiry guard (exp {expiry})"

            if reason:
                success = self._close_options_position(symbol, qty)
                if success:
                    logger.info(f"\u2705 OPTIONS CLOSE: {symbol} — {reason}")
                    closed.append(symbol)
                    # Update plan if one exists
                    try:
                        pos = db.get_position_by_symbol(symbol)
                        if pos:
                            exit_status = (
                                "target_hit" if "profit" in reason
                                else "stop_hit" if "stop" in reason
                                else "expired" if "expiry" in reason
                                else "closed"
                            )
                            _update_plan(pos["id"], {
                                "status":   exit_status,
                                "exit_ts":  datetime.now(timezone.utc).isoformat(),
                                "exit_reason": reason,
                            })
                    except Exception as _upe:
                        logger.debug(f"Plan update on close failed: {_upe}")
                else:
                    logger.warning(f"OPTIONS CLOSE failed: {symbol}")

        return closed

    # ──────────────────────────────────────────────────────────────────────────
    # Contract selection
    # ──────────────────────────────────────────────────────────────────────────

    def _find_best_contract(
        self,
        symbol: str,
        direction: str,
        max_notional: float,
    ) -> Optional[dict]:
        """
        Query Alpaca options chain and return the best contract:
          - Within int(_cfg('options.min_expiry_days', 14)) to int(_cfg('options.max_expiry_days', 35))
          - Affordable (close_price × 100 <= max_notional)
          - Liquid (open_interest >= int(_cfg('options.min_open_interest', 10)))
          - Prefer highest open_interest (most liquid)
          - Prefer ATM/slightly OTM (avoid deep OTM)
        """
        now = datetime.now()
        gte = (now + timedelta(days=int(_cfg('options.min_expiry_days', 14)))).strftime("%Y-%m-%d")
        lte = (now + timedelta(days=int(_cfg('options.max_expiry_days', 35)))).strftime("%Y-%m-%d")

        try:
            r = requests.get(
                f"{BASE_URL}/v2/options/contracts",
                headers=self.headers,
                params={
                    "underlying_symbols": symbol,
                    "expiration_date_gte": gte,
                    "expiration_date_lte": lte,
                    "type": direction,
                    "status": "active",
                    "limit": 100,
                },
                timeout=10,
            )
            r.raise_for_status()
            contracts = r.json().get("option_contracts", [])
        except Exception as e:
            logger.warning(f"Options chain fetch failed for {symbol}: {e}")
            return None

        if not contracts:
            logger.debug(f"No {direction} contracts found for {symbol} ({gte}–{lte})")
            return None

        # Get current stock price for ATM filtering
        stock_price = self._get_stock_price(symbol)

        # Filter and score candidates
        candidates = []
        for c in contracts:
            premium = c.get("close_price") or c.get("last_price")
            if premium is None:
                continue
            premium = float(premium)
            notional = premium * 100

            oi = int(c.get("open_interest") or 0)
            strike = float(c.get("strike_price", 0))

            # Affordability check
            if notional > max_notional:
                continue

            # Liquidity check
            if oi < int(_cfg('options.min_open_interest', 10)):
                continue

            # ATM proximity score (prefer strikes within 20% of stock price)
            if stock_price and stock_price > 0:
                strike_pct_diff = abs(strike - stock_price) / stock_price
                if strike_pct_diff > 0.30:
                    continue  # Skip deep OTM (>30% away)
                atm_score = 1.0 - strike_pct_diff  # Closer to ATM = higher score
            else:
                atm_score = 0.5

            candidates.append({
                **c,
                "_oi":       oi,
                "_notional": notional,
                "_atm_score": atm_score,
            })

        if not candidates:
            logger.debug(
                f"No qualifying {direction} contracts for {symbol} "
                f"after filters (budget=${max_notional:.0f}, OI>={int(_cfg('options.min_open_interest', 10))})"
            )
            return None

        # Sort: highest OI first (liquidity), then ATM proximity as tiebreaker
        candidates.sort(key=lambda c: (c["_oi"], c["_atm_score"]), reverse=True)
        best = candidates[0]

        logger.info(
            f"Options contract selected: {best['symbol']} "
            f"strike={best['strike_price']} exp={best['expiration_date']} "
            f"OI={best['_oi']} premium=${best['_notional']:.2f} "
            f"ATM_score={best['_atm_score']:.2f}"
        )
        return best

    # ──────────────────────────────────────────────────────────────────────────
    # Order execution
    # ──────────────────────────────────────────────────────────────────────────

    def _place_options_order(self, contract_symbol: str, qty: int = 1):
        """Place a market buy order for an options contract."""
        try:
            payload = {
                "symbol":         contract_symbol,
                "qty":            str(qty),
                "side":           "buy",
                "type":           "market",
                "time_in_force":  "day",
            }
            r = requests.post(
                f"{BASE_URL}/v2/orders",
                json=payload,
                headers=self.headers,
                timeout=10,
            )
            r.raise_for_status()
            order = r.json()
            return True, order.get("id", "")
        except Exception as e:
            logger.error(f"Options order failed for {contract_symbol}: {e}")
            return False, ""

    def _close_options_position(self, contract_symbol: str, qty: float) -> bool:
        """Market sell to close an options position."""
        try:
            payload = {
                "symbol":        contract_symbol,
                "qty":           str(int(qty)),
                "side":          "sell",
                "type":          "market",
                "time_in_force": "day",
            }
            r = requests.post(
                f"{BASE_URL}/v2/orders",
                json=payload,
                headers=self.headers,
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Options close failed for {contract_symbol}: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_direction(self, signal_type: str) -> str:
        if signal_type in BEARISH_SIGNALS:
            return "put"
        return "call"  # Default: bullish

    def _get_options_buying_power(self) -> Optional[float]:
        try:
            r = requests.get(f"{BASE_URL}/v2/account", headers=self.headers, timeout=10)
            r.raise_for_status()
            return float(r.json().get("options_buying_power", 0))
        except Exception as e:
            logger.warning(f"Could not fetch account: {e}")
            return None

    def _get_stock_price(self, symbol: str) -> Optional[float]:
        try:
            r = requests.get(
                f"{DATA_URL}/v2/stocks/{symbol}/trades/latest",
                headers=self.headers,
                params={"feed": "iex"},
                timeout=5,
            )
            r.raise_for_status()
            return float(r.json().get("trade", {}).get("p", 0)) or None
        except Exception:
            return None

    @staticmethod
    def _is_option_symbol(symbol: str) -> bool:
        """Option symbols follow OCC format: 6 char ticker + 6 digit date + C/P + 8 digit strike."""
        return len(symbol) > 10 and ("C0" in symbol or "P0" in symbol)

    @staticmethod
    def _parse_expiry_from_symbol(symbol: str):
        """Parse expiration date from OCC option symbol (e.g. ANAB260320C00035000 → 2026-03-20)."""
        try:
            # Find where C or P appears (after the ticker)
            for i, ch in enumerate(symbol):
                if ch in ("C", "P") and i > 0 and symbol[i-1].isdigit():
                    date_str = symbol[i-6:i]  # 6 digits before C/P
                    return datetime.strptime("20" + date_str, "%Y%m%d").date()
        except Exception:
            return None

    def _decode_option_symbol_local(self, symbol: str) -> dict:
        """Local alias for OCC symbol decoding (used in plan writing)."""
        return self.__class__._decode_static(symbol)

    @staticmethod
    def _decode_static(symbol: str):
        try:
            for i in range(len(symbol) - 1, 5, -1):
                if symbol[i] in ('C', 'P') and symbol[i-6:i].isdigit():
                    ticker    = symbol[:i-6]
                    date_str  = symbol[i-6:i]
                    direction = 'call' if symbol[i] == 'C' else 'put'
                    strike    = int(symbol[i+1:]) / 1000.0
                    expiry    = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:]}"
                    return {"ticker": ticker, "expiry": expiry, "direction": direction, "strike": strike}
        except Exception:
            pass
        return None

    @staticmethod
    def _no_trade(reason: str) -> dict:
        logger.debug(f"Options skipped: {reason}")
        return {
            "traded":    False,
            "reason":    reason,
            "contract":  None,
            "premium":   0.0,
            "notional":  0.0,
            "direction": None,
        }
