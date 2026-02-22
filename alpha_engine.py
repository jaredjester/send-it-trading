#!/usr/bin/env python3
"""
Alpha Engine - Multi-Strategy Signal Generator
Production-ready alpha generation for Pi Hedge Fund

Combines mean reversion, momentum, sector rotation, and sentiment analysis
into a unified scoring system. Pure Python + numpy + pandas only.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

from core.config import load_config


class AlphaEngine:
    """
    Multi-strategy alpha signal generator combining:
    - Mean reversion (RSI + Bollinger bands)
    - Momentum (trend following + ADX)
    - Sector rotation
    - Sentiment-enhanced signals
    """
    
    def __init__(self, config_path: str | None = None):
        """Initialize alpha engine with configuration."""
        self.config = load_config(config_path)
        acct = self.config.get("account", {})
        self.api_key = acct.get("alpaca_api_key") or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_LIVE_KEY")
        self.api_secret = acct.get("alpaca_secret_key") or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
        self.data_url = acct.get("alpaca_data_url", "https://data.alpaca.markets")
        self.feed = acct.get("data_feed", "iex")
        
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret
        }
        
        # Cache for bar data to reduce API calls
        self.bar_cache = {}
        self.cache_timestamps = {}
        self.cache_ttl = self.config.get("data", {}).get("cache_ttl_seconds", 300)
        
        log_cfg = self.config.get("logging", {})
        logging.basicConfig(
            level=log_cfg.get("level", "INFO"),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def _fetch_bars(self, symbol: str, days: int = 200) -> List[Dict]:
        """
        Fetch historical bar data from Alpaca with caching and retry logic.
        
        Args:
            symbol: Stock symbol
            days: Number of days of history to fetch
            
        Returns:
            List of bar dicts with keys: t, o, h, l, c, v, n, vw
        """
        cache_key = f"{symbol}_{days}"
        now = time.time()
        
        # Check cache
        if cache_key in self.bar_cache:
            if now - self.cache_timestamps[cache_key] < self.cache_ttl:
                return self.bar_cache[cache_key]
        
        # Calculate date range
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        
        params = {
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%dT00:00:00Z"),
            "end": end.strftime("%Y-%m-%dT23:59:59Z"),
            "feed": self.feed,
            "limit": self.config['data']['max_bars']
        }
        
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        
        # Retry logic
        for attempt in range(self.config['data']['retry_attempts']):
            try:
                response = requests.get(url, headers=self.headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                bars = data.get("bars", [])
                
                # Update cache
                self.bar_cache[cache_key] = bars
                self.cache_timestamps[cache_key] = now
                
                self.logger.info(f"Fetched {len(bars)} bars for {symbol}")
                return bars
            
            except Exception as e:
                self.logger.warning(f"Attempt {attempt + 1} failed for {symbol}: {e}")
                if attempt < self.config['data']['retry_attempts'] - 1:
                    time.sleep(self.config['data']['retry_delay_seconds'])
                else:
                    self.logger.error(f"Failed to fetch bars for {symbol} after all retries")
                    return []
        
        return []
    
    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """
        Calculate RSI (Relative Strength Index) from scratch.
        
        Args:
            prices: Array of closing prices
            period: RSI period (default 14)
            
        Returns:
            RSI value (0-100)
        """
        if len(prices) < period + 1:
            return 50.0  # Neutral if not enough data
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_sma(self, prices: np.ndarray, period: int) -> float:
        """Calculate Simple Moving Average."""
        if len(prices) < period:
            return prices[-1] if len(prices) > 0 else 0.0
        return np.mean(prices[-period:])
    
    def _calculate_std(self, prices: np.ndarray, period: int) -> float:
        """Calculate standard deviation."""
        if len(prices) < period:
            return 0.0
        return np.std(prices[-period:])
    
    def _calculate_adx(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        """
        Calculate ADX (Average Directional Index) from scratch.
        Measures trend strength (0-100, >25 indicates strong trend).
        
        Args:
            highs: Array of high prices
            lows: Array of low prices
            closes: Array of close prices
            period: ADX period (default 14)
            
        Returns:
            ADX value (0-100)
        """
        if len(closes) < period + 1:
            return 0.0
        
        # True Range
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        
        # Directional Movement
        dm_plus = np.where((highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]), 
                           np.maximum(highs[1:] - highs[:-1], 0), 0)
        dm_minus = np.where((lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]), 
                            np.maximum(lows[:-1] - lows[1:], 0), 0)
        
        # Smooth TR and DM
        atr = np.mean(tr[-period:])
        dm_plus_smooth = np.mean(dm_plus[-period:])
        dm_minus_smooth = np.mean(dm_minus[-period:])
        
        if atr == 0:
            return 0.0
        
        # Directional Indicators
        di_plus = 100 * dm_plus_smooth / atr
        di_minus = 100 * dm_minus_smooth / atr
        
        # ADX
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
        
        return dx
    
    def _mean_reversion_score(self, bars: List[Dict]) -> Dict:
        """
        Calculate mean reversion score based on RSI, Bollinger bands, and volume.
        
        Returns:
            Dict with score, signals, and trade parameters
        """
        cfg = self.config['mean_reversion']
        if not cfg['enabled'] or len(bars) < cfg['lookback_days']:
            return {"score": 0, "signals": {}, "active": False}
        
        closes = np.array([float(b['c']) for b in bars])
        volumes = np.array([float(b['v']) for b in bars])
        
        current_price = closes[-1]
        rsi = self._calculate_rsi(closes)
        sma_20 = self._calculate_sma(closes, cfg['lookback_days'])
        std_20 = self._calculate_std(closes, cfg['lookback_days'])
        
        avg_volume = np.mean(volumes[-cfg['lookback_days']:])
        current_volume = volumes[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Distance from mean in standard deviations
        if std_20 > 0:
            std_distance = (sma_20 - current_price) / std_20
        else:
            std_distance = 0
        
        # Check mean reversion conditions
        is_oversold = rsi < cfg['rsi_oversold']
        is_below_mean = std_distance > cfg['std_dev_threshold']
        has_volume_spike = volume_ratio > cfg['volume_spike_min']
        
        signals = {
            "rsi": rsi,
            "sma_20": sma_20,
            "std_distance": std_distance,
            "volume_ratio": volume_ratio,
            "is_oversold": is_oversold,
            "is_below_mean": is_below_mean,
            "has_volume_spike": has_volume_spike
        }
        
        # Score calculation (0-100)
        score = 0
        if is_oversold and is_below_mean:
            # Base score from RSI (inverse, lower RSI = higher score)
            score += (cfg['rsi_oversold'] - rsi) / cfg['rsi_oversold'] * 40
            
            # Bonus for distance below mean
            score += min(std_distance / cfg['std_dev_threshold'], 2.0) * 30
            
            # Bonus for volume confirmation
            if has_volume_spike:
                score += 30
        
        active = score > 20
        
        # Calculate trade parameters
        stop_loss = current_price * (1 - self.config['risk']['stop_loss_pct'])
        take_profit = sma_20 * 1.02  # Target slightly above mean
        
        return {
            "score": min(score, 100),
            "signals": signals,
            "active": active,
            "entry_price": current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "target_hold_days": cfg['target_hold_days']
        }
    
    def _momentum_score(self, bars: List[Dict]) -> Dict:
        """
        Calculate momentum score based on trend alignment, ADX, and volume.
        
        Returns:
            Dict with score, signals, and trade parameters
        """
        cfg = self.config['momentum']
        if not cfg['enabled'] or len(bars) < cfg['sma_long']:
            return {"score": 0, "signals": {}, "active": False}
        
        closes = np.array([float(b['c']) for b in bars])
        highs = np.array([float(b['h']) for b in bars])
        lows = np.array([float(b['l']) for b in bars])
        volumes = np.array([float(b['v']) for b in bars])
        
        current_price = closes[-1]
        sma_20 = self._calculate_sma(closes, cfg['sma_short'])
        sma_50 = self._calculate_sma(closes, cfg['sma_long'])
        adx = self._calculate_adx(highs, lows, closes)
        
        # Volume trend
        recent_volume = np.mean(volumes[-5:])
        older_volume = np.mean(volumes[-20:-5]) if len(volumes) > 20 else recent_volume
        volume_growth = recent_volume / older_volume if older_volume > 0 else 1.0
        
        # Check momentum conditions
        is_trending_up = current_price > sma_20 > sma_50
        has_strong_trend = adx > cfg['adx_threshold']
        has_volume_growth = volume_growth > cfg['volume_growth_min']
        
        signals = {
            "sma_20": sma_20,
            "sma_50": sma_50,
            "adx": adx,
            "volume_growth": volume_growth,
            "is_trending_up": is_trending_up,
            "has_strong_trend": has_strong_trend,
            "has_volume_growth": has_volume_growth
        }
        
        # Score calculation (0-100)
        score = 0
        if is_trending_up:
            # Base score for alignment
            score += 40
            
            # Bonus for trend strength (ADX)
            if has_strong_trend:
                score += min((adx - cfg['adx_threshold']) / cfg['adx_threshold'], 1.5) * 30
            
            # Bonus for volume confirmation
            if has_volume_growth:
                score += 30
        
        active = score > 30
        
        # Calculate trade parameters
        stop_loss = sma_20 * 0.95  # Stop below short-term trend
        take_profit = current_price * 1.15  # 15% target for momentum trades
        
        return {
            "score": min(score, 100),
            "signals": signals,
            "active": active,
            "entry_price": current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "target_hold_days": cfg['target_hold_days']
        }
    
    def _sentiment_enhanced_score(self, bars: List[Dict], sentiment_score: Optional[float] = None) -> Dict:
        """
        Enhance sentiment signals with technical confirmation.
        
        Args:
            bars: Historical price bars
            sentiment_score: FinBERT sentiment score (0-1, 0.5=neutral)
            
        Returns:
            Dict with score, signals, and trade parameters
        """
        cfg = self.config['sentiment']
        if not cfg['enabled'] or sentiment_score is None or len(bars) < 20:
            return {"score": 0, "signals": {}, "active": False}
        
        closes = np.array([float(b['c']) for b in bars])
        volumes = np.array([float(b['v']) for b in bars])
        
        current_price = closes[-1]
        rsi = self._calculate_rsi(closes)
        
        avg_volume = np.mean(volumes[-20:])
        current_volume = volumes[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        is_positive_sentiment = sentiment_score > cfg['positive_threshold']
        is_negative_sentiment = sentiment_score < cfg['negative_threshold']
        is_oversold = rsi < 30
        is_overbought = rsi > 70
        has_volume = volume_ratio > 1.2
        
        signals = {
            "sentiment_score": sentiment_score,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "is_positive_sentiment": is_positive_sentiment,
            "is_negative_sentiment": is_negative_sentiment,
            "technical_confirmation": False
        }
        
        score = 0
        
        # Positive sentiment + technical confirmation
        if is_positive_sentiment and is_oversold and has_volume:
            score = 80  # Strong buy
            signals["technical_confirmation"] = True
        elif is_positive_sentiment and not is_overbought:
            score = 50  # Moderate buy
            signals["technical_confirmation"] = True
        elif is_positive_sentiment and is_overbought:
            score = 0  # Skip - already priced in
            signals["technical_confirmation"] = False
        
        # Negative sentiment + breakdown pattern
        elif is_negative_sentiment and is_overbought:
            score = -80  # Strong sell signal (negative score for sells)
            signals["technical_confirmation"] = True
        elif is_negative_sentiment:
            score = -40  # Moderate sell
            signals["technical_confirmation"] = True
        
        active = abs(score) > 30
        
        # Calculate trade parameters
        stop_loss = current_price * (1 - self.config['risk']['stop_loss_pct'])
        take_profit = current_price * (1 + 0.10)
        
        return {
            "score": score,  # Can be negative for sell signals
            "signals": signals,
            "active": active,
            "entry_price": current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "target_hold_days": 3
        }
    
    def score_opportunity(self, symbol: str, bars: Optional[List[Dict]] = None, 
                         sentiment_score: Optional[float] = None, 
                         regime: str = "unknown") -> Dict:
        """
        Master scoring function combining all strategies.
        
        Args:
            symbol: Stock symbol
            bars: Optional pre-fetched bar data (will fetch if None)
            sentiment_score: Optional FinBERT sentiment score (0-1)
            regime: Market regime hint ("bull", "bear", "neutral", "unknown")
            
        Returns:
            Dict with comprehensive scoring and trade recommendation:
            {
                "score": 0-100,
                "strategy": "mean_reversion" | "momentum" | "sentiment_enhanced",
                "signals": {...detailed signal breakdown...},
                "confidence": 0-1,
                "suggested_action": "strong_buy" | "buy" | "hold" | "sell" | "strong_sell",
                "position_type": "swing" | "scalp" | "core",
                "target_hold_days": int,
                "entry_price": float,
                "stop_loss": float,
                "take_profit": float
            }
        """
        # Fetch bars if not provided
        if bars is None:
            bars = self._fetch_bars(symbol)
        
        if not bars or len(bars) < 20:
            return {
                "score": 0,
                "strategy": "none",
                "signals": {"error": "insufficient_data"},
                "confidence": 0.0,
                "suggested_action": "hold",
                "position_type": "none",
                "target_hold_days": 0,
                "entry_price": 0.0,
                "stop_loss": 0.0,
                "take_profit": 0.0
            }
        
        # Calculate all strategy scores
        mean_rev = self._mean_reversion_score(bars)
        momentum = self._momentum_score(bars)
        sentiment = self._sentiment_enhanced_score(bars, sentiment_score)
        
        # Weighted combination
        weights = {
            "mean_reversion": self.config['mean_reversion']['score_weight'],
            "momentum": self.config['momentum']['score_weight'],
            "sentiment": self.config['sentiment']['score_weight']
        }
        
        # Handle negative sentiment scores (sell signals)
        sentiment_contribution = sentiment['score'] * weights['sentiment']
        
        weighted_score = (
            mean_rev['score'] * weights['mean_reversion'] +
            momentum['score'] * weights['momentum'] +
            abs(sentiment_contribution)  # Use absolute value for total score
        )
        
        # Determine dominant strategy
        scores_dict = {
            "mean_reversion": mean_rev['score'],
            "momentum": momentum['score'],
            "sentiment_enhanced": abs(sentiment['score'])
        }
        dominant_strategy = max(scores_dict.items(), key=lambda x: x[1])
        
        # Select parameters from dominant strategy
        if dominant_strategy[0] == "mean_reversion" and mean_rev['active']:
            params = mean_rev
            position_type = "scalp"
        elif dominant_strategy[0] == "momentum" and momentum['active']:
            params = momentum
            position_type = "swing"
        elif dominant_strategy[0] == "sentiment_enhanced" and sentiment['active']:
            params = sentiment
            position_type = "swing"
        else:
            # Default to mean reversion if no strategy is clearly active
            params = mean_rev
            position_type = "hold"
        
        # Calculate confidence (0-1)
        confidence = min(weighted_score / 100, 1.0)
        
        # Adjust for regime
        if regime == "bear" and dominant_strategy[0] == "momentum":
            confidence *= 0.7  # Reduce confidence in momentum during bear markets
        elif regime == "bull" and dominant_strategy[0] == "mean_reversion":
            confidence *= 0.8  # Slightly reduce mean reversion confidence in bull markets
        
        # Determine action
        if sentiment['score'] < -50:
            suggested_action = "strong_sell"
        elif sentiment['score'] < -30:
            suggested_action = "sell"
        elif weighted_score > 70:
            suggested_action = "strong_buy"
        elif weighted_score > 50:
            suggested_action = "buy"
        else:
            suggested_action = "hold"
        
        return {
            "score": weighted_score,
            "strategy": dominant_strategy[0],
            "signals": {
                "mean_reversion": mean_rev['signals'],
                "momentum": momentum['signals'],
                "sentiment": sentiment['signals']
            },
            "confidence": confidence,
            "suggested_action": suggested_action,
            "position_type": position_type,
            "target_hold_days": params.get('target_hold_days', 5),
            "entry_price": params.get('entry_price', 0.0),
            "stop_loss": params.get('stop_loss', 0.0),
            "take_profit": params.get('take_profit', 0.0)
        }


if __name__ == "__main__":
    # Test the alpha engine
    engine = AlphaEngine()
    
    test_symbols = ["AAPL", "MSFT", "GME"]
    
    for symbol in test_symbols:
        print(f"\n{'='*60}")
        print(f"Analyzing {symbol}")
        print('='*60)
        
        result = engine.score_opportunity(symbol, sentiment_score=0.7)
        
        print(f"Score: {result['score']:.1f}")
        print(f"Strategy: {result['strategy']}")
        print(f"Confidence: {result['confidence']:.2f}")
        print(f"Action: {result['suggested_action']}")
        print(f"Position Type: {result['position_type']}")
        print(f"Entry: ${result['entry_price']:.2f}")
        print(f"Stop Loss: ${result['stop_loss']:.2f}")
        print(f"Take Profit: ${result['take_profit']:.2f}")
        print(f"Target Hold: {result['target_hold_days']} days")
