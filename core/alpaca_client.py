"""
Unified Alpaca API client for data, account, and positions.

Single source of truth for Alpaca auth and bar fetching.
Supports caching, retries, and config-based or env-based credentials.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests

from core.config import load_config


class AlpacaClient:
    """
    Unified Alpaca client with caching and retries.

    Credentials: env vars (ALPACA_API_LIVE_KEY, ALPACA_API_SECRET) or master_config.json
    """

    def __init__(
        self,
        config: dict | None = None,
        base_url: str | None = None,
        data_url: str | None = None,
        cache_ttl: int = 300,
        retry_attempts: int = 3,
        retry_delay: int = 2,
    ):
        """
        Args:
            config: Optional config dict. If None, loads from master_config.json.
            base_url: Override account/trading API base (default: paper or live from config)
            data_url: Override data API base
            cache_ttl: Bar cache TTL in seconds
            retry_attempts: Number of retries for failed requests
            retry_delay: Delay between retries in seconds
        """
        self.config = config or load_config()
        acct = self.config.get("account", {})
        self.api_key = (
            acct.get("alpaca_api_key")
            or os.getenv("APCA_API_KEY_ID")
            or os.getenv("ALPACA_API_LIVE_KEY")
        )
        self.api_secret = (
            acct.get("alpaca_secret_key")
            or os.getenv("APCA_API_SECRET_KEY")
            or os.getenv("ALPACA_API_SECRET")
        )

        # Validate credentials
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "Alpaca API credentials not found. Set ALPACA_API_LIVE_KEY/ALPACA_API_SECRET "
                "environment variables or add to master_config.json"
            )

        self.base_url = base_url or acct.get("alpaca_base_url", "https://paper-api.alpaca.markets")
        self.data_url = data_url or acct.get("alpaca_data_url", "https://data.alpaca.markets")
        self.feed = acct.get("data_feed", "iex")
        self.cache_ttl = cache_ttl
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

        self._headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        self._bar_cache: dict[str, list] = {}
        self._cache_timestamps: dict[str, float] = {}

    def _request(self, url: str, params: dict | None = None, timeout: int = 10) -> Any:
        """Execute GET request with retries."""
        for attempt in range(self.retry_attempts):
            try:
                r = requests.get(url, headers=self._headers, params=params, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt < self.retry_attempts - 1:
                    time.sleep(self.retry_delay)
                    continue
                # Log the final failure and return None instead of raising
                print(f"API request failed after {self.retry_attempts} attempts: {e}")
                return None
        return None

    def fetch_bars(
        self,
        symbol: str,
        days: int = 60,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch daily bars for a symbol.

        Returns:
            DataFrame with columns: time, open, high, low, close, volume
        """
        cache_key = f"{symbol}_{days}"
        now = time.time()
        if use_cache and cache_key in self._bar_cache:
            if now - self._cache_timestamps.get(cache_key, 0) < self.cache_ttl:
                bars = self._bar_cache[cache_key]
                return self._bars_to_df(bars)

        end = datetime.utcnow()
        start = end - timedelta(days=days + 5)
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT00:00:00Z"),
            "feed": self.feed,
            "limit": min(days, 200),
        }
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        data = self._request(url, params)
        if not data:
            return pd.DataFrame()
        bars = data.get("bars", [])
        if not bars:
            return pd.DataFrame()

        if use_cache:
            self._bar_cache[cache_key] = bars
            self._cache_timestamps[cache_key] = now

        return self._bars_to_df(bars)

    def fetch_bars_raw(self, symbol: str, days: int = 60) -> list[dict]:
        """Fetch bars as raw list (Alpaca format: t, o, h, l, c, v) for AlphaEngine."""
        cache_key = f"{symbol}_{days}"
        now = time.time()
        if cache_key in self._bar_cache:
            if now - self._cache_timestamps.get(cache_key, 0) < self.cache_ttl:
                return self._bar_cache[cache_key]

        end = datetime.utcnow()
        start = end - timedelta(days=days + 5)
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT00:00:00Z"),
            "feed": self.feed,
            "limit": min(days, 200),
        }
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        data = self._request(url, params)
        if not data:
            return []
        bars = data.get("bars", [])
        if bars:
            self._bar_cache[cache_key] = bars
            self._cache_timestamps[cache_key] = now
        return bars

    def _bars_to_df(self, bars: list) -> pd.DataFrame:
        """Convert Alpaca bar list to standardized DataFrame."""
        df = pd.DataFrame(bars)
        if df.empty:
            return df
        df = df.rename(columns={
            "t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
        })
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(float)
        return df

    def get_account(self) -> dict:
        """Fetch account info."""
        url = f"{self.base_url}/v2/account"
        return self._request(url) or {}

    def get_positions(self) -> list:
        """Fetch open positions."""
        url = f"{self.base_url}/v2/positions"
        return self._request(url) or []

    def get_portfolio_state(self) -> dict:
        """Convenience: account + positions for portfolio decisions."""
        account = self.get_account()
        positions = self.get_positions()
        return {
            "account": account,
            "positions": positions,
            "portfolio_value": float(account.get("portfolio_value", 0)),
            "cash": float(account.get("cash", 0)),
            "equity": float(account.get("equity", 0)),
            "buying_power": float(account.get("buying_power", 0)),
            "daytrade_count": int(account.get("daytrade_count", 0)),
        }

    def clear_cache(self):
        """Clear bar cache (e.g. before fresh run)."""
        self._bar_cache.clear()
        self._cache_timestamps.clear()
