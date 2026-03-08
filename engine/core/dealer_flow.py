#!/usr/bin/env python3
"""
Dealer Flow Engine — GEX / VEX / Charm / IV Rank / Gamma Flip

Computes structural dealer-positioning signals from live Alpaca options chains.

Signals:
  GEX  (Gamma Exposure)    — positive=dealers long gamma (mean-revert), negative=dealers short (momentum)
  VEX  (Vanna Exposure)    — IV drop forces dealers to buy stock → upward pressure
  Charm                    — daily delta decay hedging → intraday drift bias
  IV Rank                  — IV percentile vs chain distribution (sell >70, buy <20)
  Gamma Flip Level         — price where GEX crosses zero (key pivot/magnet)
  VRP  (Vol Risk Premium)  — IV − RealizedVol (sell premium when IV >> RV)

All signals normalised to [-10, +10] before being consumed by AlphaEngine.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.05
CONTRACT_MULTIPLIER = 100
CHAIN_CACHE_TTL = 900      # 15 min
HIST_WINDOW_DAYS = 30


# ── Black-Scholes Greeks ──────────────────────────────────────────────────────

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))

def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = _d1(S, K, T, r, sigma)
        return _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0

def bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vanna = −pdf(d1)·d2/sigma"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = _d1(S, K, T, r, sigma)
        d2 = d1 - sigma * math.sqrt(T)
        return -_norm_pdf(d1) * d2 / sigma
    except (ValueError, ZeroDivisionError):
        return 0.0

def bs_charm(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Charm = dDelta/dTime"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = _d1(S, K, T, r, sigma)
        d2 = d1 - sigma * math.sqrt(T)
        sqrtT = math.sqrt(T)
        return -_norm_pdf(d1) * (2 * r * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT)
    except (ValueError, ZeroDivisionError):
        return 0.0


# ── Dealer Flow Engine ────────────────────────────────────────────────────────

class DealerFlowEngine:
    """
    Computes GEX, VEX, Charm, IV Rank, Gamma Flip, and VRP
    from live Alpaca options chains.
    """

    def __init__(self):
        self._alpaca_key = (os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_LIVE_KEY")
                            or os.getenv("ALPACA_API_KEY_ID"))
        self._alpaca_secret = (os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_LIVE_SECRET")
                               or os.getenv("ALPACA_API_SECRET"))
        self._base = os.getenv("ALPACA_LIVE_BASE", "https://api.alpaca.markets")
        self._data_base = os.getenv("ALPACA_DATA_BASE", "https://data.alpaca.markets")
        self._chain_cache: dict[str, tuple[float, list]] = {}

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._alpaca_key or "",
            "APCA-API-SECRET-KEY": self._alpaca_secret or "",
            "Accept": "application/json",
        }

    def _fetch_chain(self, symbol: str) -> list[dict]:
        now = time.time()
        cached = self._chain_cache.get(symbol)
        if cached and now - cached[0] < CHAIN_CACHE_TTL:
            return cached[1]
        try:
            today = date.today()
            url = f"{self._base}/v2/options/contracts"
            params = {
                "underlying_symbols": symbol,
                "expiration_date_gte": today.isoformat(),
                "expiration_date_lte": (today + timedelta(days=90)).isoformat(),
                "limit": 1000,
                "status": "active",
            }
            r = requests.get(url, headers=self._headers(), params=params, timeout=15)
            if not r.ok:
                logger.warning(f"DealerFlow chain {symbol}: {r.status_code}")
                return []
            contracts = r.json().get("option_contracts", [])
            self._chain_cache[symbol] = (now, contracts)
            logger.debug(f"DealerFlow: {len(contracts)} contracts for {symbol}")
            return contracts
        except Exception as e:
            logger.warning(f"DealerFlow chain error {symbol}: {e}")
            return []

    def _fetch_bars(self, symbol: str) -> list[float]:
        try:
            end = date.today()
            start = end - timedelta(days=HIST_WINDOW_DAYS + 10)
            url = f"{self._data_base}/v2/stocks/{symbol}/bars"
            r = requests.get(url, headers=self._headers(), params={
                "start": start.isoformat(), "end": end.isoformat(),
                "timeframe": "1Day", "limit": 45,
                "adjustment": "split", "feed": "sip",
            }, timeout=10)
            if not r.ok:
                return []
            return [b["c"] for b in r.json().get("bars", []) if "c" in b]
        except Exception:
            return []

    def _realized_vol(self, symbol: str) -> float:
        closes = self._fetch_bars(symbol)
        if len(closes) < 5:
            return 0.25
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(log_returns) < 2:
            return 0.25
        n = len(log_returns)
        mean = sum(log_returns) / n
        variance = sum((x - mean) ** 2 for x in log_returns) / (n - 1)
        return math.sqrt(variance) * math.sqrt(252)

    def _tte(self, expiry_str: str) -> float:
        try:
            exp = date.fromisoformat(expiry_str)
            return max((exp - date.today()).days / 365.0, 0.0)
        except Exception:
            return 0.0

    def _compute_gex(self, contracts: list, spot: float) -> tuple[float, dict]:
        total = 0.0
        by_strike: dict[float, float] = {}
        for c in contracts:
            try:
                K = float(c.get("strike_price", 0))
                iv = float(c.get("implied_volatility", 0) or c.get("close_price", 0) or 0.25)
                oi = int(c.get("open_interest", 0))
                T = self._tte(c.get("expiration_date", ""))
                if K <= 0 or oi <= 0 or T <= 0:
                    continue
                sigma = iv if iv > 0.01 else 0.25
                gex = bs_gamma(spot, K, T, RISK_FREE_RATE, sigma) * oi * CONTRACT_MULTIPLIER * spot ** 2
                if str(c.get("type", "call")).lower() == "put":
                    gex = -gex
                total += gex
                by_strike[K] = by_strike.get(K, 0.0) + gex
            except (ValueError, TypeError, KeyError):
                continue
        return total, by_strike

    def _compute_vex(self, contracts: list, spot: float) -> float:
        total = 0.0
        for c in contracts:
            try:
                K = float(c.get("strike_price", 0))
                iv = float(c.get("implied_volatility", 0) or 0.25)
                oi = int(c.get("open_interest", 0))
                T = self._tte(c.get("expiration_date", ""))
                if K <= 0 or oi <= 0 or T <= 0:
                    continue
                sigma = iv if iv > 0.01 else 0.25
                total += bs_vanna(spot, K, T, RISK_FREE_RATE, sigma) * oi * CONTRACT_MULTIPLIER * spot * sigma
            except (ValueError, TypeError, KeyError):
                continue
        return total

    def _compute_charm(self, contracts: list, spot: float) -> float:
        total = 0.0
        for c in contracts:
            try:
                K = float(c.get("strike_price", 0))
                iv = float(c.get("implied_volatility", 0) or 0.25)
                oi = int(c.get("open_interest", 0))
                T = self._tte(c.get("expiration_date", ""))
                if K <= 0 or oi <= 0 or T <= 0:
                    continue
                sigma = iv if iv > 0.01 else 0.25
                total += bs_charm(spot, K, T, RISK_FREE_RATE, sigma) * oi * CONTRACT_MULTIPLIER
            except (ValueError, TypeError, KeyError):
                continue
        return total

    def _gamma_flip(self, contracts: list, spot: float, n_steps: int = 200) -> Optional[float]:
        low, high = spot * 0.85, spot * 1.15
        step = (high - low) / n_steps
        price_range = [low + i * step for i in range(n_steps)]
        gex_series = [self._compute_gex(contracts, p)[0] for p in price_range]
        for i in range(1, len(gex_series)):
            if gex_series[i - 1] * gex_series[i] < 0:
                p0, p1 = price_range[i - 1], price_range[i]
                g0, g1 = gex_series[i - 1], gex_series[i]
                dg = g1 - g0
                return (p0 + (p1 - p0) * (-g0) / dg) if dg != 0 else price_range[i]
        return None

    def _iv_rank(self, contracts: list) -> float:
        ivs = [float(c.get("implied_volatility", 0) or 0) for c in contracts]
        ivs = [iv for iv in ivs if 0.01 < iv < 5.0]
        if not ivs:
            return 50.0
        iv_min, iv_max = min(ivs), max(ivs)
        current_iv = sum(ivs) / len(ivs)
        if iv_max == iv_min:
            return 50.0
        return round(((current_iv - iv_min) / (iv_max - iv_min)) * 100, 1)

    def compute(self, symbol: str, spot: float) -> dict:
        """
        Compute all dealer flow signals for a symbol.

        Returns dict with gex_norm, vex_norm, charm_norm, iv_rank,
        gamma_flip, vrp, regime, strategy_bias.
        All *_norm values are in [-10, +10] for direct use in AlphaEngine.
        """
        if spot <= 0:
            return self._empty()

        contracts = self._fetch_chain(symbol)
        if not contracts:
            return self._empty()

        total_gex, by_strike = self._compute_gex(contracts, spot)
        total_vex = self._compute_vex(contracts, spot)
        total_charm = self._compute_charm(contracts, spot)
        iv_rank = self._iv_rank(contracts)
        flip = self._gamma_flip(contracts, spot)
        rv = self._realized_vol(symbol)

        ivs = [float(c.get("implied_volatility", 0) or 0) for c in contracts if c.get("implied_volatility")]
        mean_iv = sum(ivs) / max(len(ivs), 1)
        vrp = round(mean_iv - rv, 4)

        # Regime
        if total_gex > 0:
            regime = "positive_gex"
        elif total_gex < 0:
            regime = "negative_gex"
        else:
            regime = "neutral"

        # Strategy bias
        if total_gex < 0 and iv_rank < 40:
            strategy_bias = "buy_directional"
        elif total_gex > 0 and iv_rank > 65:
            strategy_bias = "sell_premium"
        elif total_gex < 0:
            strategy_bias = "momentum"
        elif iv_rank < 20:
            strategy_bias = "buy_vol"
        elif iv_rank > 80:
            strategy_bias = "sell_premium"
        else:
            strategy_bias = "mean_revert"

        # Normalize to [-10, +10]
        scale_gex = max((spot ** 2) * 1e9, 1)
        gex_norm = max(-10.0, min(10.0, (total_gex / scale_gex) * 10.0))
        scale_vex = max(spot * 1e8, 1)
        vex_norm = max(-10.0, min(10.0, (total_vex / scale_vex) * 10.0))
        charm_norm = max(-10.0, min(10.0, total_charm / 1e5))

        result = {
            "gex": round(total_gex, 0),
            "gex_norm": round(gex_norm, 2),
            "vex_norm": round(vex_norm, 2),
            "charm_norm": round(charm_norm, 2),
            "iv_rank": iv_rank,
            "gamma_flip": round(flip, 2) if flip else None,
            "above_flip": (flip is not None and spot > flip),
            "vrp": vrp,
            "realized_vol": round(rv, 4),
            "mean_iv": round(mean_iv, 4),
            "regime": regime,
            "strategy_bias": strategy_bias,
            "contracts_used": len(contracts),
        }
        logger.info(
            f"DealerFlow {symbol}: GEX={gex_norm:+.1f} VEX={vex_norm:+.1f} "
            f"IV_rank={iv_rank:.0f} flip={flip} regime={regime}"
        )
        return result

    def _empty(self) -> dict:
        return {
            "gex": 0, "gex_norm": 0.0, "vex_norm": 0.0, "charm_norm": 0.0,
            "iv_rank": 50.0, "gamma_flip": None, "above_flip": False,
            "vrp": 0.0, "realized_vol": 0.25, "mean_iv": 0.25,
            "regime": "neutral", "strategy_bias": "mean_revert",
            "contracts_used": 0,
        }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    price = float(sys.argv[2]) if len(sys.argv) > 2 else 570.0
    engine = DealerFlowEngine()
    r = engine.compute(sym, price)
    for k, v in r.items():
        print(f"  {k:<18} {v}")
