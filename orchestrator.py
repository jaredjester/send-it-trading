"""
Master Orchestrator — The brain that coordinates all trading subsystems.

This replaces the old analyze_and_notify() pure-sentiment flow with a
multi-layer decision pipeline:

    News/Screener → Alpha Scoring → Risk Gate → RL Gate → Size → Execute

Every decision is logged. Every trade has context. Nothing fires without
passing through risk checks and RL confidence gating.

Call run_orchestrated_cycle() from main.py every 30 minutes.
"""

import os
import sys
import json
import logging
import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("orchestrator")

BASE_DIR = Path(__file__).parent
STOCKBOT_DIR = BASE_DIR.parent  # ~/shared/stockbot/
ADAPTIVE_DIR = STOCKBOT_DIR / "adaptive"

# Conviction Manager — contrarian/catalyst position handling
try:
    from conviction_manager import ConvictionManager, load_conviction_manager
    _conviction_mgr = None

    def get_conviction_manager():
        global _conviction_mgr
        if _conviction_mgr is None:
            _conviction_mgr = load_conviction_manager()
        return _conviction_mgr
except ImportError:
    def get_conviction_manager():
        return None

# Monte Carlo tail risk checking
try:
    from risk_fortress import check_tail_risk_monte_carlo
    MONTE_CARLO_ENABLED = True
except ImportError:
    MONTE_CARLO_ENABLED = False
    logger.warning("Monte Carlo tail risk checking not available")

# High-ROI Scanners (Gap & Catalyst)
try:
    from scanners.opportunity_finder import OpportunityFinder
    SCANNERS_ENABLED = True
except ImportError:
    SCANNERS_ENABLED = False
    logger.warning("High-ROI scanners not available - using basic screening only")

# IC Integration (Signal → Outcome tracking)
try:
    from evaluation.ic_integration import record_trade_entry, record_trade_exit
    IC_TRACKING_ENABLED = True
except ImportError:
    IC_TRACKING_ENABLED = False
    logger.warning("IC tracking not available - signal quality not measured")

# Alpaca endpoints
ALPACA_BASE = "https://api.alpaca.markets"
ALPACA_DATA = "https://data.alpaca.markets"


def _get_keys():
    key = os.getenv("ALPACA_API_LIVE_KEY") or os.getenv("APCA_API_KEY_ID", "")
    secret = os.getenv("ALPACA_API_SECRET") or os.getenv("APCA_API_SECRET_KEY", "")
    if not key or not secret:
        config_path = BASE_DIR / "master_config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                acct = cfg.get("account", {})
                key = key or acct.get("alpaca_api_key", "")
                secret = secret or acct.get("alpaca_secret_key", "")
            except Exception as e:
                logger.debug(f"Could not load config for credentials: {e}")
    return key or "", secret or ""


def _headers():
    k, s = _get_keys()
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def _api_get(url, params=None, timeout=10):
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def _get_historical_returns(symbol: str, days: int = 90) -> list:
    """
    Fetch historical daily returns for Monte Carlo analysis.
    
    Args:
        symbol: Stock symbol
        days: Number of days of history (default 90)
    
    Returns:
        List of daily returns (e.g., [0.02, -0.01, 0.03])
    """
    try:
        from datetime import datetime, timedelta
        
        end = datetime.now()
        start = end - timedelta(days=days + 10)  # Extra buffer
        
        url = f"{ALPACA_DATA}/v2/stocks/{symbol}/bars"
        params = {
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "timeframe": "1D",
            "feed": "iex",
            "limit": days + 10
        }
        
        data = _api_get(url, params=params, timeout=15)
        if not data or 'bars' not in data:
            logger.warning(f"No bars returned for {symbol}")
            return []
        
        bars = data['bars']
        if len(bars) < 20:
            logger.warning(f"Only {len(bars)} bars for {symbol} - need 20+ for Monte Carlo")
            return []
        
        # Calculate daily returns
        closes = [float(bar['c']) for bar in bars]
        returns = []
        for i in range(1, len(closes)):
            ret = (closes[i] - closes[i-1]) / closes[i-1]
            returns.append(ret)
        
        logger.debug(f"Got {len(returns)} daily returns for {symbol}")
        return returns
        
    except Exception as e:
        logger.error(f"Failed to get historical returns for {symbol}: {e}")
        return []


def _fetch_bars(symbol, days=60):
    end = datetime.utcnow()
    start = end - timedelta(days=days + 5)
    params = {
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end.strftime("%Y-%m-%dT00:00:00Z"),
        "feed": "iex", "limit": days
    }
    data = _api_get(f"{ALPACA_DATA}/v2/stocks/{symbol}/bars", params)
    if not data:
        return pd.DataFrame()
    bars = data.get("bars", [])
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df = df.rename(columns={"t": "time", "o": "open", "h": "high",
                            "l": "low", "c": "close", "v": "volume"})
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


# ==================================================================
# Portfolio State
# ==================================================================

class PortfolioState:
    """Snapshot of current portfolio for decision-making."""

    def __init__(self):
        self.account = _api_get(f"{ALPACA_BASE}/v2/account") or {}
        self.positions = _api_get(f"{ALPACA_BASE}/v2/positions") or []
        self.portfolio_value = float(self.account.get("portfolio_value", 0))
        self.cash = float(self.account.get("cash", 0))
        self.equity = float(self.account.get("equity", 0))
        self.buying_power = float(self.account.get("buying_power", 0))
        self.daytrade_count = int(self.account.get("daytrade_count", 0))

        # Derived
        invested = self.equity - self.cash
        self.portfolio_heat = invested / self.equity if self.equity > 0 else 0
        self.cash_reserve_pct = self.cash / self.equity if self.equity > 0 else 0
        self.position_map = {p["symbol"]: p for p in self.positions}
        self.position_count = len(self.positions)

        # Concentration
        if self.positions and self.equity > 0:
            weights = [float(p["market_value"]) / self.equity for p in self.positions]
            self.max_position_pct = max(weights) if weights else 0
            self.max_position_symbol = max(
                self.positions, key=lambda p: float(p["market_value"])
            )["symbol"] if self.positions else None
            # HHI (Herfindahl-Hirschman Index) — >0.25 is highly concentrated
            self.hhi = sum(w**2 for w in weights)
        else:
            self.max_position_pct = 0
            self.max_position_symbol = None
            self.hhi = 0

    def summary(self):
        return {
            "value": self.portfolio_value,
            "cash": self.cash,
            "positions": self.position_count,
            "heat": round(self.portfolio_heat, 3),
            "cash_reserve": round(self.cash_reserve_pct, 3),
            "max_position": f"{self.max_position_symbol}={self.max_position_pct:.1%}",
            "hhi": round(self.hhi, 4),
            "daytrades": self.daytrade_count
        }


# ==================================================================
# Signal Pipeline
# ==================================================================

def score_symbol(symbol, bars_df, sentiment_score=None):
    """
    Multi-factor scoring for a single symbol.
    Returns a dict with score (0-100), signals, and suggested action.
    """
    if bars_df.empty or len(bars_df) < 20:
        return {"symbol": symbol, "score": 0, "action": "skip", "reason": "insufficient data"}

    close = bars_df["close"]
    volume = bars_df["volume"]
    high = bars_df["high"]
    low = bars_df["low"]

    current_price = float(close.iloc[-1])
    scores = {}

    # --- RSI ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    if np.isnan(rsi):
        rsi = 50

    if rsi < 30:
        scores["rsi"] = {"value": rsi, "signal": "oversold", "score": 80 + (30 - rsi)}
    elif rsi < 45:
        scores["rsi"] = {"value": rsi, "signal": "approaching_oversold", "score": 60}
    elif rsi > 70:
        scores["rsi"] = {"value": rsi, "signal": "overbought", "score": 20}
    else:
        scores["rsi"] = {"value": rsi, "signal": "neutral", "score": 50}

    # --- MACD ---
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    macd_hist = (macd_line - signal_line).iloc[-1]
    macd_prev = (macd_line - signal_line).iloc[-2] if len(close) > 1 else 0

    if macd_hist > 0 and macd_prev <= 0:
        scores["macd"] = {"value": float(macd_hist), "signal": "bullish_cross", "score": 85}
    elif macd_hist > 0:
        scores["macd"] = {"value": float(macd_hist), "signal": "bullish", "score": 65}
    elif macd_hist < 0 and macd_prev >= 0:
        scores["macd"] = {"value": float(macd_hist), "signal": "bearish_cross", "score": 15}
    else:
        scores["macd"] = {"value": float(macd_hist), "signal": "bearish", "score": 35}

    # --- SMA Trend ---
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else sma20
    trend_aligned = current_price > sma20 > sma50 if not np.isnan(sma50) else False

    if trend_aligned:
        scores["trend"] = {"signal": "aligned_up", "score": 75}
    elif current_price > sma20:
        scores["trend"] = {"signal": "above_sma20", "score": 60}
    elif current_price < sma20 and current_price < sma50:
        scores["trend"] = {"signal": "below_both", "score": 25}
    else:
        scores["trend"] = {"signal": "mixed", "score": 45}

    # --- Volume ---
    avg_vol = volume.rolling(20).mean().iloc[-1]
    current_vol = volume.iloc[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1

    if vol_ratio > 2.0:
        scores["volume"] = {"ratio": float(vol_ratio), "signal": "surge", "score": 80}
    elif vol_ratio > 1.3:
        scores["volume"] = {"ratio": float(vol_ratio), "signal": "above_avg", "score": 65}
    elif vol_ratio < 0.5:
        scores["volume"] = {"ratio": float(vol_ratio), "signal": "dry", "score": 30}
    else:
        scores["volume"] = {"ratio": float(vol_ratio), "signal": "normal", "score": 50}

    # --- ATR (for stop loss) ---
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    atr_pct = atr / current_price if current_price > 0 else 0

    # --- Mean Reversion Signal ---
    std20 = close.rolling(20).std().iloc[-1]
    distance_from_mean = (current_price - sma20) / std20 if std20 > 0 else 0
    mean_reversion_score = 0
    if distance_from_mean < -2 and rsi < 35 and vol_ratio > 1.3:
        mean_reversion_score = min(95, 70 + abs(distance_from_mean) * 10)

    # --- Momentum Signal ---
    momentum_score = 0
    if trend_aligned and rsi > 40 and rsi < 65 and vol_ratio > 1.0:
        momentum_score = 60 + min(30, (rsi - 40))

    # --- ADX (trend strength) ---
    if len(bars_df) >= 14:
        plus_dm = high.diff().where(lambda x: x > 0, 0)
        minus_dm = (-low.diff()).where(lambda x: x > 0, 0)
        atr14 = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / atr14
        minus_di = 100 * minus_dm.rolling(14).mean() / atr14
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(14).mean().iloc[-1]
        if np.isnan(adx):
            adx = 20
    else:
        adx = 20

    scores["adx"] = {"value": float(adx), "trending": adx > 25}

    # --- Sentiment Enhancement ---
    sentiment_bonus = 0
    if sentiment_score is not None and sentiment_score > 0.7:
        if rsi < 40:  # Positive sentiment on oversold stock = strong
            sentiment_bonus = 20
        elif rsi < 60:  # Positive sentiment, not overbought
            sentiment_bonus = 10
        # If overbought, sentiment bonus = 0 (already priced in)

    # --- Composite Score ---
    weights = {"rsi": 0.20, "macd": 0.20, "trend": 0.15,
               "volume": 0.15, "mean_reversion": 0.15, "momentum": 0.15}

    composite = (
        weights["rsi"] * scores["rsi"]["score"] +
        weights["macd"] * scores["macd"]["score"] +
        weights["trend"] * scores["trend"]["score"] +
        weights["volume"] * scores["volume"]["score"] +
        weights["mean_reversion"] * mean_reversion_score +
        weights["momentum"] * momentum_score +
        sentiment_bonus
    )

    composite = max(0, min(100, composite))

    # --- Strategy Classification ---
    if mean_reversion_score > 70:
        strategy = "mean_reversion"
        hold_days = 2
    elif momentum_score > 70:
        strategy = "momentum"
        hold_days = 7
    elif sentiment_bonus > 10:
        strategy = "sentiment_enhanced"
        hold_days = 3
    else:
        strategy = "multi_factor"
        hold_days = 5

    # --- Action ---
    if composite >= 75:
        action = "strong_buy"
    elif composite >= 60:
        action = "buy"
    elif composite <= 25:
        action = "strong_sell"
    elif composite <= 40:
        action = "sell"
    else:
        action = "hold"

    # --- Stop Loss & Take Profit ---
    stop_loss = current_price - (atr * 2)
    take_profit = current_price + (atr * 3)  # 1.5:1 R:R minimum

    return {
        "symbol": symbol,
        "score": round(composite, 1),
        "action": action,
        "strategy": strategy,
        "confidence": round(composite / 100, 3),
        "hold_days": hold_days,
        "current_price": current_price,
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "atr": round(float(atr), 4),
        "atr_pct": round(float(atr_pct), 4),
        "rsi": round(float(rsi), 1),
        "adx": round(float(adx), 1),
        "mean_reversion_score": round(mean_reversion_score, 1),
        "momentum_score": round(momentum_score, 1),
        "sentiment_bonus": sentiment_bonus,
        "signals": scores
    }


# ==================================================================
# Risk Gate
# ==================================================================

class RiskGate:
    """Pre-trade risk checks. Must pass ALL checks to trade."""

    MAX_POSITION_PCT = 0.20       # 20% max per position
    MAX_SECTOR_PCT = 0.30         # 30% max per sector
    MIN_CASH_RESERVE = 0.10       # 10% minimum cash
    MAX_RISK_PER_TRADE = 0.02     # 2% max risk per trade
    MIN_TRADE_SIZE = 10.0         # $10 minimum trade
    MAX_DAILY_TRADES = 2          # PDT safety (reserves 1)
    CIRCUIT_BREAKER_DD = 0.03     # 3% intraday drawdown halts buying

    def __init__(self, state_file=None):
        self.state_file = state_file or str(BASE_DIR / "risk_state.json")
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load risk state ({self.state_file}): {e}")
        return {
            "day_trades_today": 0,
            "consecutive_losses": 0,
            "day_start_value": 0,
            "high_water_mark": 0,
            "date": "",
            "trades_today": []
        }

    def _save_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save risk state: {e}")

    def new_day(self, portfolio_value):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self.state.get("date") != today:
            self.state["date"] = today
            self.state["day_trades_today"] = 0
            self.state["consecutive_losses"] = 0
            self.state["day_start_value"] = portfolio_value
            self.state["trades_today"] = []
            hwm = self.state.get("high_water_mark", 0)
            self.state["high_water_mark"] = max(hwm, portfolio_value)
            self._save_state()

    def can_buy(self, symbol, dollar_amount, portfolio_state, sector="other"):
        """Full pre-trade risk check. Returns (allowed, reason, adjusted_size)."""
        ps = portfolio_state
        reasons = []

        # Circuit breaker: intraday drawdown
        day_start = self.state.get("day_start_value", ps.portfolio_value)
        if day_start > 0:
            intraday_dd = (day_start - ps.portfolio_value) / day_start
            if intraday_dd > self.CIRCUIT_BREAKER_DD:
                return False, f"Circuit breaker: {intraday_dd:.1%} intraday drawdown", 0

        # Circuit breaker: consecutive losses
        if self.state.get("consecutive_losses", 0) >= 3:
            return False, "Circuit breaker: 3 consecutive losses today", 0

        # PDT guard
        if self.state.get("day_trades_today", 0) >= self.MAX_DAILY_TRADES:
            return False, f"PDT guard: {self.state['day_trades_today']}/{self.MAX_DAILY_TRADES} day trades used", 0

        # Cash reserve
        cash_after = ps.cash - dollar_amount
        reserve_after = cash_after / ps.equity if ps.equity > 0 else 0
        if reserve_after < self.MIN_CASH_RESERVE:
            max_spend = ps.cash - (ps.equity * self.MIN_CASH_RESERVE)
            if max_spend < self.MIN_TRADE_SIZE:
                return False, f"Cash reserve: ${ps.cash:.2f} cash, need {self.MIN_CASH_RESERVE:.0%} reserve", 0
            dollar_amount = max_spend
            reasons.append(f"Size reduced to maintain {self.MIN_CASH_RESERVE:.0%} cash reserve")

        # Position concentration
        if symbol in ps.position_map:
            existing = float(ps.position_map[symbol]["market_value"])
            new_total = existing + dollar_amount
            new_pct = new_total / ps.equity if ps.equity > 0 else 1
            if new_pct > self.MAX_POSITION_PCT:
                max_add = (ps.equity * self.MAX_POSITION_PCT) - existing
                if max_add < self.MIN_TRADE_SIZE:
                    return False, f"Position limit: {symbol} already at {existing/ps.equity:.1%}", 0
                dollar_amount = max_add
                reasons.append(f"Size capped at {self.MAX_POSITION_PCT:.0%} position limit")

        # Minimum trade size
        if dollar_amount < self.MIN_TRADE_SIZE:
            return False, f"Below minimum trade size (${dollar_amount:.2f} < ${self.MIN_TRADE_SIZE})", 0

        # Portfolio heat
        if ps.portfolio_heat > 0.90:
            return False, f"Portfolio heat: {ps.portfolio_heat:.1%} deployed, need more cash", 0

        reason = "; ".join(reasons) if reasons else "All checks passed"
        return True, reason, dollar_amount

    def record_trade(self, symbol, is_day_trade=False, win=None):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        self.state["trades_today"].append({
            "symbol": symbol, "time": datetime.utcnow().isoformat()
        })
        if is_day_trade:
            self.state["day_trades_today"] = self.state.get("day_trades_today", 0) + 1
        if win is not None:
            if win:
                self.state["consecutive_losses"] = 0
            else:
                self.state["consecutive_losses"] = self.state.get("consecutive_losses", 0) + 1
        self._save_state()

    def calculate_position_size(self, entry_price, stop_loss, portfolio_value):
        """Risk-based position sizing: risk 2% of portfolio per trade."""
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            risk_per_share = entry_price * 0.05  # Default 5% stop

        risk_amount = portfolio_value * self.MAX_RISK_PER_TRADE
        shares = int(risk_amount / risk_per_share)
        dollar_amount = shares * entry_price

        # Cap at 20% of portfolio
        max_dollar = portfolio_value * self.MAX_POSITION_PCT
        if dollar_amount > max_dollar:
            shares = int(max_dollar / entry_price)
            dollar_amount = shares * entry_price

        return {
            "shares": max(shares, 0),
            "dollar_amount": round(dollar_amount, 2),
            "risk_amount": round(risk_amount, 2),
            "risk_per_share": round(risk_per_share, 2)
        }


# ==================================================================
# RL Integration Gate
# ==================================================================

def get_rl_recommendation():
    """Read the Q-learner's current recommendation from adaptive system."""
    try:
        q_table_path = ADAPTIVE_DIR / "q_table.json"
        q_stats_path = ADAPTIVE_DIR / "q_stats.json"

        if not q_stats_path.exists():
            return {"action": "hold", "confidence": 0, "episodes": 0}

        with open(q_stats_path) as f:
            stats = json.load(f)

        episodes = stats.get("episodes_learned", 0)
        epsilon = stats.get("epsilon", 1.0)

        # If RL has learned enough, read its recommendation
        if episodes >= 3 and q_table_path.exists():
            with open(q_table_path) as f:
                data = json.load(f)
            q_table = data.get("q_table", {})

            # Find the state with the most visits
            visits = data.get("visit_counts", {})
            if visits:
                best_state = max(visits, key=lambda s: sum(visits[s].values()))
                actions = q_table.get(best_state, {})
                if actions:
                    best_action = max(actions, key=actions.get)
                    return {
                        "action": best_action,
                        "q_value": actions[best_action],
                        "confidence": min(1.0, episodes / 20),
                        "episodes": episodes,
                        "epsilon": epsilon
                    }

        return {"action": "hold", "confidence": 0, "episodes": episodes}
    except Exception as e:
        logger.error(f"RL recommendation failed: {e}")
        return {"action": "hold", "confidence": 0, "episodes": 0}


def rl_gate(alpha_score, rl_rec):
    """
    Combine alpha engine score with RL recommendation.
    70% alpha, 30% RL.
    """
    alpha_conf = alpha_score.get("confidence", 0)
    rl_conf = rl_rec.get("confidence", 0)
    rl_action = rl_rec.get("action", "hold")

    # If RL says defensive and alpha says buy → dampen
    if rl_action == "defensive" and alpha_score.get("action") in ("buy", "strong_buy"):
        alpha_conf *= 0.5
        logger.info("RL override: defensive dampened buy signal")

    # If RL says aggressive_buy and alpha says buy → boost
    if rl_action == "aggressive_buy" and alpha_score.get("action") in ("buy", "strong_buy"):
        alpha_conf = min(1.0, alpha_conf * 1.25)
        logger.info("RL boost: aggressive_buy amplified buy signal")

    # If RL says reduce → only allow sells
    if rl_action == "reduce" and alpha_score.get("action") in ("buy", "strong_buy"):
        return 0.0  # Block buys when RL says reduce

    # Blended confidence
    if rl_conf > 0:
        blended = 0.7 * alpha_conf + 0.3 * rl_conf
    else:
        blended = alpha_conf  # RL not ready, trust alpha only

    return round(blended, 4)


# ==================================================================
# Exit Scanner
# ==================================================================

def scan_exits(portfolio_state):
    """
    Scan current positions for exit signals.
    Returns list of {symbol, action, reason, urgency}.

    Respects conviction positions — conviction symbols get custom rules
    instead of default zombie/concentration/stop limits.
    """
    exits = []
    cm = get_conviction_manager()

    for pos in portfolio_state.positions:
        symbol = pos["symbol"]
        pnl_pct = float(pos.get("unrealized_plpc", 0))
        market_val = float(pos.get("market_value", 0))
        entry_price = float(pos.get("avg_entry_price", 0))
        current_price = float(pos.get("current_price", 0))

        # ── Conviction override check ────────────────────────────
        overrides = cm.get_risk_overrides(symbol) if cm else None
        is_conviction = overrides and overrides.get("is_conviction", False)

        if is_conviction:
            conv = cm.get_conviction(symbol)
            if conv:
                # Conviction positions: only exit on max pain or conviction-specific stop
                max_pain = conv.get("max_pain_price", 0)
                conv_stop = overrides.get("stop_loss_pct", -25)

                if max_pain > 0 and current_price <= max_pain:
                    exits.append({
                        "symbol": symbol, "action": "sell",
                        "reason": f"CONVICTION MAX PAIN: ${current_price:.2f} ≤ ${max_pain:.2f}",
                        "urgency": "critical",
                        "conviction": True
                    })
                elif pnl_pct < (conv_stop / 100):
                    exits.append({
                        "symbol": symbol, "action": "sell",
                        "reason": f"Conviction stop hit: {pnl_pct:.0%} < {conv_stop}%",
                        "urgency": "high",
                        "conviction": True
                    })
                else:
                    # Conviction holds — skip normal exit rules
                    logger.info(f"Conviction HOLD {symbol}: score={conv['current_score']:.0f}, "
                              f"phase={conv['phase']}, pnl={pnl_pct:.1%}")
                continue  # Skip normal exit logic for conviction symbols

        # ── Normal exit rules (non-conviction) ───────────────────

        # Zombie killer: down >60% and worth <$5
        if pnl_pct < -0.60 and market_val < 5:
            exits.append({
                "symbol": symbol, "action": "sell",
                "reason": f"Zombie: {pnl_pct:.0%} loss, ${market_val:.2f} value",
                "urgency": "high"
            })
            continue

        # Deep loss exit: down >40% — cut the bleeding
        if pnl_pct < -0.40 and market_val > 5:
            exits.append({
                "symbol": symbol, "action": "sell",
                "reason": f"Deep loss: {pnl_pct:.0%}",
                "urgency": "medium"
            })
            continue

        # Concentration trim: any position >25% of portfolio
        weight = market_val / portfolio_state.equity if portfolio_state.equity > 0 else 0
        if weight > 0.25:
            trim_to = portfolio_state.equity * 0.20
            trim_amount = market_val - trim_to
            if trim_amount > 10:
                exits.append({
                    "symbol": symbol, "action": "trim",
                    "reason": f"Concentration: {weight:.1%} > 25% limit",
                    "urgency": "medium",
                    "trim_amount": round(trim_amount, 2)
                })

        # Take profit on winners: >30% gain, take partial
        if pnl_pct > 0.30 and market_val > 20:
            exits.append({
                "symbol": symbol, "action": "trim",
                "reason": f"Take profit: {pnl_pct:.0%} gain",
                "urgency": "low",
                "trim_amount": round(market_val * 0.25, 2)  # Trim 25%
            })

    return exits


# ==================================================================
# Universe Screening
# ==================================================================

SCAN_UNIVERSE = [
    # Large cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AMD", "INTC", "CRM", "ORCL",
    # Large cap other
    "JPM", "BAC", "WFC", "JNJ", "PFE", "UNH", "XOM", "CVX",
    "WMT", "COST", "HD", "NKE", "DIS", "NFLX", "TSLA",
    # Mid cap momentum candidates
    "PLTR", "SOFI", "RKLB", "HOOD", "AFRM", "UPST", "NET",
    "CRWD", "SNOW", "DDOG", "MDB", "PANW",
    # ETFs for sector rotation
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU",
    # High volume movers (refresh periodically)
    "SPY", "QQQ", "IWM",
]


def screen_universe(max_candidates=10):
    """
    Screen the universe for top trading candidates.
    Returns sorted list of scored symbols.
    """
    candidates = []
    for symbol in SCAN_UNIVERSE:
        try:
            bars = _fetch_bars(symbol, days=60)
            if bars.empty or len(bars) < 20:
                continue

            score = score_symbol(symbol, bars)
            if score["score"] >= 55:  # Only consider decent scores
                candidates.append(score)

        except Exception as e:
            logger.debug(f"Screening {symbol} failed: {e}")
            continue

        # Rate limit: don't hammer the API
        time.sleep(0.2)

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:max_candidates]


# ==================================================================
# Master Orchestrator
# ==================================================================

async def run_orchestrated_cycle(send_telegram=None):
    """
    The complete decision cycle. Replaces analyze_and_notify().

    1. Snapshot portfolio state
    2. Check for exit signals (zombies, concentration, profit-taking)
    3. Screen universe for entry candidates
    4. Score candidates with multi-factor alpha
    5. Gate through risk checks
    6. Gate through RL confidence
    7. Size positions
    8. Execute (buys and sells)
    9. Update adaptive/RL system
    10. Report
    """
    logger.info("═══ Orchestrated Cycle Start ═══")

    try:
        # Late imports for stockbot compatibility
        from alpacaFunctions import (
            buy_stock_asset, sell_stock_asset, is_market_hours,
            trading_client, place_trailing_stop_order
        )
        from telegramMessaging import send_telegram_message
    except ImportError as e:
        logger.error(f"Import error (run from stockbot dir): {e}")
        return

    if not is_market_hours():
        logger.info("Market closed, skipping cycle")
        return

    # 1. Portfolio snapshot
    ps = PortfolioState()
    risk = RiskGate()
    risk.new_day(ps.portfolio_value)
    rl_rec = get_rl_recommendation()

    logger.info(f"Portfolio: ${ps.portfolio_value:.2f} | Cash: ${ps.cash:.2f} | "
                f"Heat: {ps.portfolio_heat:.1%} | Positions: {ps.position_count}")
    logger.info(f"RL: {rl_rec['action']} (conf={rl_rec['confidence']:.2f}, "
                f"episodes={rl_rec['episodes']})")

    actions_taken = []

    # 1b. Conviction update cycle
    cm = get_conviction_manager()
    conviction_actions = []
    if cm and cm.get_active_convictions():
        try:
            conviction_actions = cm.run_update_cycle(
                portfolio_value=ps.portfolio_value,
                positions=ps.positions
            )
            active = cm.get_active_convictions()
            for sym, conv in active.items():
                logger.info(f"Conviction {sym}: score={conv['current_score']:.0f} "
                          f"phase={conv['phase']}")
        except Exception as e:
            logger.error(f"Conviction update failed: {e}")

    # Handle conviction actions (accumulation, phase changes, alerts)
    for ca in conviction_actions:
        ca_type = ca.get("type", "")
        ca_symbol = ca.get("symbol", "")

        if ca_type == "ACCUMULATE" and ps.cash > 15:
            # Conviction dip-buy
            add_amt = ca.get("amount", 25)
            if add_amt <= ps.cash * 0.5:  # Don't blow all cash on one add
                try:
                    price = ca.get("price", 0)
                    shares = max(1, int(add_amt / price)) if price > 0 else 0
                    if shares > 0:
                        result = await buy_stock_asset(ca_symbol, qty=shares)
                        if result:
                            actions_taken.append(
                                f"CONVICTION ADD {ca_symbol} x{shares} "
                                f"(dip {ca.get('pct_from_entry', 0):+.1f}%, "
                                f"score={ca.get('conviction_score', 0):.0f})"
                            )
                except Exception as e:
                    logger.error(f"Conviction accumulate {ca_symbol} failed: {e}")

        elif ca_type in ("ABANDON", "AUTO_CLOSED"):
            actions_taken.append(f"⚠️ CONVICTION {ca_type} {ca_symbol}: {ca.get('reason', '')}")

        elif ca_type == "PHASE_CHANGE":
            actions_taken.append(
                f"📊 {ca_symbol} conviction: {ca.get('from')} → {ca.get('to')} "
                f"(score={ca.get('score', 0):.0f})"
            )

        elif ca_type in ("DEADLINE_EXPIRED", "MAX_HOLD_EXCEEDED"):
            actions_taken.append(f"⏰ {ca_symbol}: {ca.get('reason', '')}")

    # 2. Exit signals (conviction-aware — respects conviction holds)
    exits = scan_exits(ps)
    for exit_sig in exits:
        symbol = exit_sig["symbol"]
        action = exit_sig["action"]
        reason = exit_sig["reason"]

        if action == "sell":
            try:
                # Get exit price for IC tracking
                exit_price = exit_sig.get("current_price", 0)
                
                result = await sell_stock_asset(symbol, reason)
                
                # Record exit for IC tracking
                if IC_TRACKING_ENABLED and exit_price > 0:
                    try:
                        # Get benchmark return (approximate SPY)
                        benchmark_return = 0.0  # TODO: Fetch actual SPY return
                        record_trade_exit(
                            symbol=symbol,
                            exit_price=exit_price,
                            exit_reason=reason,
                            benchmark_return=benchmark_return
                        )
                    except Exception as e:
                        logger.error(f"IC exit recording failed: {e}")
                
                actions_taken.append(f"SELL {symbol}: {reason}")
                risk.record_trade(symbol, win=False)
                logger.info(f"EXIT {symbol}: {reason}")
            except Exception as e:
                logger.error(f"Failed to sell {symbol}: {e}")

        elif action == "trim":
            trim_amt = exit_sig.get("trim_amount", 0)
            if trim_amt > 10:
                pos = ps.position_map.get(symbol, {})
                price = float(pos.get("current_price", 1))
                trim_shares = max(1, int(trim_amt / price))
                try:
                    # Sell partial position
                    result = await sell_stock_asset(symbol, reason)
                    actions_taken.append(f"TRIM {symbol} (~${trim_amt:.0f}): {reason}")
                    logger.info(f"TRIM {symbol}: {reason}")
                except Exception as e:
                    logger.error(f"Failed to trim {symbol}: {e}")

    # 3-6. Screen, score, risk-check, RL-gate
    if ps.cash > 15 and ps.cash_reserve_pct > 0.08:  # Only screen if we have capital
        # Get candidates from both traditional screening and high-ROI scanners
        candidates = screen_universe(max_candidates=5)
        
        # Add scanner opportunities if available
        if SCANNERS_ENABLED:
            try:
                finder = OpportunityFinder()
                scanner_opps = finder.get_top_opportunities(limit=5)
                
                for opp in scanner_opps:
                    # Convert scanner format to candidate format
                    scanner_candidate = {
                        'symbol': opp['symbol'],
                        'score': opp['score'],
                        'confidence': opp['score'] / 100.0,  # Normalize to 0-1
                        'action': 'strong_buy',  # High-ROI opportunities
                        'strategy': f"{opp['opportunity_type'].lower()}_scanner",
                        'current_price': opp.get('current_price') or opp.get('price', 0),
                        'stop_loss': opp.get('stop_loss', opp.get('current_price', 0) * 0.95),
                        'take_profit': opp.get('take_profit', opp.get('current_price', 0) * 1.15),
                        'atr_pct': opp.get('atr_pct', 0.02),
                        'rsi': opp.get('rsi', 50),
                        'adx': opp.get('adx', 25),
                        'opportunity_type': opp['opportunity_type'],
                        'entry_time': opp['entry_time'],
                        'from_scanner': True
                    }
                    candidates.append(scanner_candidate)
                
                logger.info(f"Added {len(scanner_opps)} scanner opportunities to candidate pool")
            except Exception as e:
                logger.error(f"Scanner integration failed: {e}")

        for candidate in candidates:
            symbol = candidate["symbol"]
            alpha_score = candidate["score"]
            action = candidate["action"]
            confidence = candidate["confidence"]

            if action not in ("buy", "strong_buy"):
                continue

            # Already own it?
            if symbol in ps.position_map:
                logger.debug(f"Skip {symbol}: already in portfolio")
                continue

            # RL gate
            final_conf = rl_gate(candidate, rl_rec)
            if final_conf < 0.55:
                logger.info(f"Skip {symbol}: confidence {final_conf:.2f} < 0.55")
                continue

            # Monte Carlo tail risk check
            mc_approved_size = None
            if MONTE_CARLO_ENABLED:
                try:
                    # Get historical returns
                    hist_returns = _get_historical_returns(symbol, days=90)
                    
                    if len(hist_returns) >= 20:
                        # Initial position size estimate (will be refined by position sizer)
                        initial_size_pct = min(0.20, final_conf * 0.30)  # Max 20% or conf * 30%
                        
                        # Run tail risk check
                        mc_approved, mc_size, mc_analysis = check_tail_risk_monte_carlo(
                            symbol=symbol,
                            historical_returns=hist_returns,
                            kelly_fraction=final_conf,
                            proposed_size=initial_size_pct,
                            max_drawdown_tolerance=0.25  # 25% max p95 drawdown
                        )
                        
                        if not mc_approved:
                            logger.warning(f"MONTE CARLO BLOCK: {symbol} tail risk too high")
                            continue
                        
                        if mc_size < initial_size_pct * 0.5:  # Reduced by 50%+
                            logger.warning(
                                f"{symbol} Monte Carlo size reduction: "
                                f"{initial_size_pct:.1%} → {mc_size:.1%}"
                            )
                        
                        mc_approved_size = mc_size
                        
                except Exception as e:
                    logger.error(f"Monte Carlo check failed for {symbol}: {e}")

            # Position sizing
            sizing = risk.calculate_position_size(
                candidate["current_price"],
                candidate["stop_loss"],
                ps.portfolio_value
            )
            
            # Apply Monte Carlo size adjustment if available
            if mc_approved_size is not None:
                mc_dollar_size = ps.portfolio_value * mc_approved_size
                if mc_dollar_size < sizing["dollar_amount"]:
                    logger.info(
                        f"{symbol} applying Monte Carlo size limit: "
                        f"${sizing['dollar_amount']:.2f} → ${mc_dollar_size:.2f}"
                    )
                    sizing["dollar_amount"] = mc_dollar_size
                    sizing["shares"] = int(mc_dollar_size / candidate["current_price"])

            if sizing["shares"] < 1:
                logger.info(f"Skip {symbol}: position size < 1 share")
                continue

            # Risk gate
            allowed, reason, adj_size = risk.can_buy(
                symbol, sizing["dollar_amount"], ps
            )

            if not allowed:
                logger.info(f"BLOCKED {symbol}: {reason}")
                continue

            # 7-8. Execute
            try:
                result = await buy_stock_asset(symbol, qty=sizing["shares"])
                if result:
                    # Record entry for IC tracking
                    if IC_TRACKING_ENABLED:
                        try:
                            record_trade_entry(
                                symbol=symbol,
                                entry_price=candidate["current_price"],
                                strategy=candidate["strategy"],
                                alpha_score=alpha_score,
                                signal_details={
                                    'rsi': candidate.get('rsi', 50),
                                    'adx': candidate.get('adx', 25),
                                    'volume_ratio': candidate.get('volume_ratio', 1.0),
                                    'confidence': final_conf,
                                    'from_scanner': candidate.get('from_scanner', False)
                                },
                                quantity=sizing["shares"]
                            )
                        except Exception as e:
                            logger.error(f"IC entry recording failed: {e}")
                    
                    # Place trailing stop
                    try:
                        trail_pct = max(3.0, min(10.0, candidate["atr_pct"] * 200))
                        await place_trailing_stop_order(
                            symbol=symbol,
                            qty=sizing["shares"],
                            side="sell",
                            trail_percent=trail_pct
                        )
                    except Exception as e:
                        logger.error(f"Trailing stop failed for {symbol}: {e}")

                    actions_taken.append(
                        f"BUY {symbol} x{sizing['shares']} "
                        f"(score={alpha_score:.0f}, "
                        f"strategy={candidate['strategy']}, "
                        f"conf={final_conf:.2f})"
                    )
                    risk.record_trade(symbol)
                    logger.info(f"BUY {symbol}: {sizing['shares']} shares @ "
                                f"${candidate['current_price']:.2f}")
            except Exception as e:
                logger.error(f"Buy failed for {symbol}: {e}")

    # 9. Update adaptive system
    try:
        sys.path.insert(0, str(ADAPTIVE_DIR))
        from adaptive_integration import run_adaptive_cycle
        adaptive_result = run_adaptive_cycle()
        logger.info(f"Adaptive cycle: {adaptive_result.get('regime', '?')} | "
                     f"RL: {adaptive_result.get('rl_action', '?')}")
    except Exception as e:
        logger.debug(f"Adaptive cycle skipped: {e}")

    # 10. Report
    if actions_taken and send_telegram:
        msg = "🧠 <b>Orchestrator Report</b>\n\n"
        msg += f"Portfolio: ${ps.portfolio_value:.2f} | Cash: ${ps.cash:.2f}\n"
        msg += f"Regime: {rl_rec.get('action', '?')} | "
        msg += f"Episodes: {rl_rec.get('episodes', 0)}\n\n"
        # Conviction summary
        if cm:
            conv_summary = cm.get_summary()
            if conv_summary:
                msg += "<b>Convictions:</b>\n"
                for cs in conv_summary:
                    msg += f"  {cs}\n"
                msg += "\n"
        msg += "<b>Actions:</b>\n"
        for a in actions_taken:
            msg += f"• {a}\n"
        try:
            await send_telegram_message(msg)
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    elif not actions_taken:
        logger.info("No actions taken this cycle")

    logger.info(f"═══ Cycle Complete: {len(actions_taken)} actions ═══")
    return {"actions": actions_taken, "portfolio": ps.summary()}


# ==================================================================
# CLI
# ==================================================================
def main():
    """CLI entry point. Usage: orchestrator [scan|exits|portfolio|cycle]."""
    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
    key, secret = _get_keys()
    if not key or not secret:
        logger.error("Missing Alpaca credentials. Set ALPACA_API_LIVE_KEY / ALPACA_API_SECRET")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "cycle":
        asyncio.run(run_orchestrated_cycle())
        return
        print("Scanning universe...")
        results = screen_universe(max_candidates=10)
        for r in results:
            print(f"  {r['symbol']:6s} score={r['score']:5.1f} "
                  f"action={r['action']:11s} strategy={r['strategy']:18s} "
                  f"RSI={r['rsi']:5.1f} ADX={r['adx']:5.1f}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        print("Scanning exits...")
        ps = PortfolioState()
        exits = scan_exits(ps)
        for e in exits:
            print(f"  {e['symbol']:8s} {e['action']:5s} — {e['reason']}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        ps = PortfolioState()
        print(json.dumps(ps.summary(), indent=2))
        return
    # Default: dry run
    print("Running full orchestrated cycle (dry run — no execution)...")
    ps = PortfolioState()
    print(f"Portfolio: {json.dumps(ps.summary(), indent=2)}")
    print("\nExit signals:")
    for e in scan_exits(ps):
        print(f"  {e['symbol']:8s} {e['action']:5s} — {e['reason']}")
    print("\nTop candidates:")
    results = screen_universe(max_candidates=5)
    for r in results:
        print(f"  {r['symbol']:6s} score={r['score']:5.1f} {r['action']:11s} "
              f"({r['strategy']}) RSI={r['rsi']:.0f} ADX={r['adx']:.0f}")


if __name__ == "__main__":
    main()
