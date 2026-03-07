"""
Regime Detector — Identifies market conditions for strategy adjustment.

Detects: trending (up/down), mean-reverting, high/low volatility.
Uses only price data from Alpaca, no external dependencies beyond numpy.
"""

import os
import logging
from datetime import datetime, timedelta

try:
    import numpy as np
except ImportError:
    np = None

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("adaptive.regime")

ALPACA_DATA = os.getenv("APCA_DATA_URL", "https://data.alpaca.markets")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID", "")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY", "")


class RegimeDetector:
    """
    Multi-signal market regime detector.

    Analyzes SPY (as a market proxy) plus individual stock data
    to determine the current market environment.
    """

    def __init__(self, config=None):
        self.config = config or {}
        rc = self.config.get("regime", {})
        self.vol_window = rc.get("volatility_window", 20)
        self.trend_window = rc.get("trend_window", 50)
        self.lookback_days = rc.get("regime_lookback_days", 30)
        self.high_vol_thresh = rc.get("high_vol_threshold", 0.025)
        self.strong_trend_thresh = rc.get("strong_trend_threshold", 25)
        self._headers = {
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }

    def _fetch_bars(self, symbol, days=60):
        """Fetch daily bars from Alpaca data API."""
        if not requests:
            return []
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        url = f"{ALPACA_DATA}/v2/stocks/{symbol}/bars"
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT00:00:00Z"),
            "feed": "iex",
            "limit": days
        }
        try:
            r = requests.get(url, headers=self._headers, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("bars", [])
        except Exception as e:
            logger.error(f"Failed to fetch bars for {symbol}: {e}")
            return []

    def _calc_atr(self, bars, period=14):
        """Calculate Average True Range."""
        if not np or len(bars) < period + 1:
            return 0
        trs = []
        for i in range(1, len(bars)):
            high = bars[i]["h"]
            low = bars[i]["l"]
            prev_close = bars[i - 1]["c"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        if len(trs) < period:
            return np.mean(trs) if trs else 0
        return np.mean(trs[-period:])

    def _calc_adx(self, bars, period=14):
        """Simplified ADX calculation."""
        if not np or len(bars) < period + 2:
            return 0

        plus_dm = []
        minus_dm = []
        trs = []

        for i in range(1, len(bars)):
            high = bars[i]["h"]
            low = bars[i]["l"]
            prev_high = bars[i - 1]["h"]
            prev_low = bars[i - 1]["l"]
            prev_close = bars[i - 1]["c"]

            up = high - prev_high
            down = prev_low - low
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        if len(trs) < period:
            return 0

        # Smoothed values (Wilder's smoothing)
        atr = np.mean(trs[:period])
        plus_di_smooth = np.mean(plus_dm[:period])
        minus_di_smooth = np.mean(minus_dm[:period])

        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
            plus_di_smooth = (plus_di_smooth * (period - 1) + plus_dm[i]) / period
            minus_di_smooth = (minus_di_smooth * (period - 1) + minus_dm[i]) / period

        if atr == 0:
            return 0

        plus_di = 100 * plus_di_smooth / atr
        minus_di = 100 * minus_di_smooth / atr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0

        dx = 100 * abs(plus_di - minus_di) / di_sum
        return dx

    def detect(self, symbol="SPY"):
        """
        Detect current market regime using SPY as proxy.

        Returns dict with:
        - regime: str (trending_up, trending_down, high_volatility,
                      low_volatility, mean_reverting)
        - volatility: float (annualized)
        - trend_strength: float (ADX value)
        - trend_direction: float (% above/below SMA)
        - confidence: float (0-1)
        """
        bars = self._fetch_bars(symbol, days=self.lookback_days + 20)
        if not bars or not np:
            return {
                "regime": "unknown", "volatility": 0,
                "trend_strength": 0, "trend_direction": 0,
                "confidence": 0
            }

        closes = np.array([b["c"] for b in bars])

        # --- Volatility ---
        if len(closes) >= 2:
            returns = np.diff(closes) / closes[:-1]
            daily_vol = np.std(returns[-self.vol_window:])
            annual_vol = daily_vol * np.sqrt(252)
        else:
            daily_vol = 0
            annual_vol = 0

        # --- Trend ---
        if len(closes) >= self.vol_window:
            sma = np.mean(closes[-self.vol_window:])
            trend_dir = (closes[-1] - sma) / sma if sma > 0 else 0
        else:
            trend_dir = 0

        # --- ADX (trend strength) ---
        adx = self._calc_adx(bars)

        # --- ATR ---
        atr = self._calc_atr(bars)
        atr_pct = atr / closes[-1] if closes[-1] > 0 else 0

        # --- Classify ---
        if daily_vol > self.high_vol_thresh:
            regime = "high_volatility"
            confidence = min(1.0, daily_vol / self.high_vol_thresh)
        elif adx > self.strong_trend_thresh and trend_dir > 0.02:
            regime = "trending_up"
            confidence = min(1.0, adx / 50)
        elif adx > self.strong_trend_thresh and trend_dir < -0.02:
            regime = "trending_down"
            confidence = min(1.0, adx / 50)
        elif adx < 15:
            regime = "mean_reverting"
            confidence = min(1.0, (20 - adx) / 20)
        else:
            regime = "low_volatility"
            confidence = 0.5

        result = {
            "regime": regime,
            "volatility": round(float(annual_vol), 4),
            "daily_vol": round(float(daily_vol), 6),
            "trend_strength": round(float(adx), 2),
            "trend_direction": round(float(trend_dir), 4),
            "atr_pct": round(float(atr_pct), 4),
            "confidence": round(float(confidence), 4),
            "symbol": symbol,
            "bars_used": len(bars),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(
            f"Regime: {regime} | Vol: {annual_vol:.1%} | "
            f"ADX: {adx:.1f} | Trend: {trend_dir:+.2%}"
        )

        return result

    def detect_multi(self, symbols=None):
        """
        Detect regime across multiple symbols for a broader view.
        Default: SPY + QQQ + IWM (large cap, tech, small cap).
        """
        if symbols is None:
            symbols = ["SPY", "QQQ", "IWM"]

        results = {}
        regimes = []
        for sym in symbols:
            r = self.detect(sym)
            results[sym] = r
            regimes.append(r["regime"])

        # Consensus regime (majority vote)
        from collections import Counter
        regime_counts = Counter(regimes)
        consensus = regime_counts.most_common(1)[0][0]

        # Average metrics
        avg_vol = np.mean([r["volatility"] for r in results.values()]) if np else 0
        avg_adx = np.mean([r["trend_strength"] for r in results.values()]) if np else 0

        return {
            "consensus_regime": consensus,
            "per_symbol": results,
            "avg_volatility": round(float(avg_vol), 4),
            "avg_trend_strength": round(float(avg_adx), 2),
            "agreement": regime_counts[consensus] / len(regimes)
        }
