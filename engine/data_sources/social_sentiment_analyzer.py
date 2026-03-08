#!/usr/bin/env python3
"""
Social Sentiment Analyzer — Enhanced sentiment analysis using Twitter/X data.

Based on Invest Assist's Twitter integration, this module provides:
1. Real-time Twitter sentiment for individual stocks
2. Cashtag ($TICKER) trending analysis
3. Social volume tracking and anomaly detection
4. Sentiment scoring with confidence levels
5. Integration with existing Reddit sentiment in Send It bot

This complements the existing Reddit/StockTwits sentiment in the bot
with more comprehensive Twitter data and better sentiment models.
"""

import logging
import requests
import json
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import sys
from pathlib import Path

# Add parent directory to path for imports

logger = logging.getLogger("social_sentiment_analyzer")

try:
    from core.dynamic_config import cfg as _cfg
except ImportError:
    def _cfg(key: str, default):
        return default

class SocialSentimentAnalyzer:
    def __init__(self):
        self.twitter_api_base = _cfg("twitter.api_endpoint", "https://api.investassist.com/api/twitter")
        self.request_timeout = _cfg("sentiment.request_timeout", 10)
        self.cache_duration = _cfg("sentiment.cache_duration_minutes", 15)
        self.sentiment_cache = {}

        # Sentiment keywords for basic text analysis backup
        self.bullish_keywords = {
            'moon', 'rocket', 'bull', 'calls', 'buy', 'long', 'bullish', 'pump',
            'breakout', 'rally', 'squeeze', 'diamond', 'hold', 'strong', 'gap up',
            'earnings beat', 'upgrade', 'target raised', 'momentum', 'surge'
        }

        self.bearish_keywords = {
            'bear', 'puts', 'sell', 'short', 'bearish', 'dump', 'crash', 'drop',
            'breakdown', 'weak', 'gap down', 'earnings miss', 'downgrade',
            'target cut', 'risk', 'concern', 'decline', 'fall', 'red'
        }

    def _make_twitter_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make Twitter API request with error handling."""
        try:
            url = f"{self.twitter_api_base}/{endpoint}"
            response = requests.post(url, json=params or {}, timeout=self.request_timeout)

            if response.status_code == 200:
                return response.json()
            else:
                logger.debug(f"Twitter API error {response.status_code} for {endpoint}")
                return None

        except Exception as e:
            logger.debug(f"Twitter request failed for {endpoint}: {e}")
            return None

    def _extract_sentiment_from_text(self, text: str) -> Tuple[float, str]:
        """
        Basic sentiment extraction from text as fallback.
        Returns (sentiment_score, confidence_level)
        sentiment_score: -1.0 to 1.0 (bearish to bullish)
        confidence_level: 'high', 'medium', 'low'
        """
        if not text:
            return 0.0, 'low'

        text_lower = text.lower()

        # Count bullish vs bearish keywords
        bullish_count = sum(1 for word in self.bullish_keywords if word in text_lower)
        bearish_count = sum(1 for word in self.bearish_keywords if word in text_lower)

        total_sentiment_words = bullish_count + bearish_count

        if total_sentiment_words == 0:
            return 0.0, 'low'

        # Calculate sentiment score
        sentiment_score = (bullish_count - bearish_count) / max(total_sentiment_words, 1)

        # Determine confidence based on number of sentiment indicators
        if total_sentiment_words >= 3:
            confidence = 'high'
        elif total_sentiment_words >= 1:
            confidence = 'medium'
        else:
            confidence = 'low'

        return sentiment_score, confidence

    def get_twitter_sentiment_for_symbol(self, symbol: str) -> Dict:
        """
        Get comprehensive Twitter sentiment analysis for a specific symbol.
        Uses Invest Assist's Twitter search API.
        """
        cache_key = f"twitter_{symbol}"

        # Check cache first
        if cache_key in self.sentiment_cache:
            cached_data, timestamp = self.sentiment_cache[cache_key]
            if datetime.now() - timestamp < timedelta(minutes=self.cache_duration):
                return cached_data

        # Search for cashtag mentions
        search_query = f"${symbol}"
        twitter_data = self._make_twitter_request("twitter-search", {
            "searchQuery": search_query
        })

        if not twitter_data or not twitter_data.get("tweets"):
            # Return neutral sentiment if no data
            result = {
                "symbol": symbol,
                "sentiment_score": 0.0,
                "confidence": "low",
                "tweet_count": 0,
                "bullish_ratio": 0.5,
                "volume_rank": "low",
                "trending": False,
                "top_keywords": [],
                "sentiment_label": "neutral"
            }
            self.sentiment_cache[cache_key] = (result, datetime.now())
            return result

        tweets = twitter_data["tweets"]

        # Analyze sentiment from all tweets
        sentiment_scores = []
        all_text = []
        recent_tweets = []

        for tweet in tweets:
            tweet_text = tweet.get("text", "")
            if not tweet_text:
                continue

            all_text.append(tweet_text)

            # Extract sentiment from individual tweet
            score, confidence = self._extract_sentiment_from_text(tweet_text)
            sentiment_scores.append(score)

            # Track recent tweets (last 24h) for volume analysis
            try:
                tweet_time = datetime.fromisoformat(tweet.get("created_at", "").replace("Z", "+00:00"))
                if datetime.now().replace(tzinfo=tweet_time.tzinfo) - tweet_time < timedelta(days=1):
                    recent_tweets.append(tweet)
            except:
                recent_tweets.append(tweet)  # Include if can't parse date

        # Calculate overall metrics
        if sentiment_scores:
            avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
            bullish_count = sum(1 for score in sentiment_scores if score > 0.1)
            bearish_count = sum(1 for score in sentiment_scores if score < -0.1)
            total_sentiment_tweets = bullish_count + bearish_count

            if total_sentiment_tweets > 0:
                bullish_ratio = bullish_count / total_sentiment_tweets
            else:
                bullish_ratio = 0.5
        else:
            avg_sentiment = 0.0
            bullish_ratio = 0.5

        # Determine confidence level
        if len(tweets) >= 20:
            confidence = "high"
        elif len(tweets) >= 5:
            confidence = "medium"
        else:
            confidence = "low"

        # Analyze volume and trending
        volume_rank = "low"
        trending = False

        if len(recent_tweets) >= 50:
            volume_rank = "high"
            trending = True
        elif len(recent_tweets) >= 20:
            volume_rank = "medium"
        elif len(recent_tweets) >= 10:
            volume_rank = "normal"

        # Extract top keywords (excluding common words)
        all_words = []
        for text in all_text:
            words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
            all_words.extend(words)

        # Filter common words and get top keywords
        common_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her',
                       'was', 'one', 'our', 'had', 'have', 'what', 'said', 'each', 'which',
                       'get', 'his', 'him', 'has', 'how', symbol.lower()}

        filtered_words = [word for word in all_words if word not in common_words and len(word) > 2]
        top_keywords = [word for word, count in Counter(filtered_words).most_common(5)]

        # Determine sentiment label
        if avg_sentiment > 0.3:
            sentiment_label = "very_bullish"
        elif avg_sentiment > 0.1:
            sentiment_label = "bullish"
        elif avg_sentiment > -0.1:
            sentiment_label = "neutral"
        elif avg_sentiment > -0.3:
            sentiment_label = "bearish"
        else:
            sentiment_label = "very_bearish"

        result = {
            "symbol": symbol,
            "sentiment_score": round(avg_sentiment, 3),
            "confidence": confidence,
            "tweet_count": len(tweets),
            "recent_tweet_count": len(recent_tweets),
            "bullish_ratio": round(bullish_ratio, 3),
            "volume_rank": volume_rank,
            "trending": trending,
            "top_keywords": top_keywords,
            "sentiment_label": sentiment_label,
            "raw_sentiment_scores": sentiment_scores[:10]  # Sample for debugging
        }

        # Cache result
        self.sentiment_cache[cache_key] = (result, datetime.now())

        logger.debug(f"Twitter sentiment for {symbol}: {sentiment_label} "
                    f"(score: {avg_sentiment:.2f}, tweets: {len(tweets)})")

        return result

    def get_trending_tickers(self) -> List[Dict]:
        """
        Get currently trending stock tickers from Twitter.
        Looks for unusual volume in cashtag mentions.
        """
        # Get latest trending data
        trending_data = self._make_twitter_request("latest-tweets")

        if not trending_data or not trending_data.get("tweets"):
            return []

        # Extract cashtags from recent tweets
        cashtag_counts = defaultdict(int)
        cashtag_sentiment = defaultdict(list)

        for tweet in trending_data["tweets"][:200]:  # Analyze recent 200 tweets
            text = tweet.get("text", "")

            # Find all cashtags ($TICKER)
            cashtags = re.findall(r'\$([A-Z]{1,5})', text.upper())

            for ticker in cashtags:
                cashtag_counts[ticker] += 1
                score, _ = self._extract_sentiment_from_text(text)
                cashtag_sentiment[ticker].append(score)

        # Filter and rank trending tickers
        trending_tickers = []
        min_mentions = int(_cfg("sentiment.min_trending_mentions", 5))

        for ticker, count in cashtag_counts.items():
            if count < min_mentions:
                continue

            # Calculate average sentiment for this ticker
            scores = cashtag_sentiment[ticker]
            avg_sentiment = sum(scores) / len(scores) if scores else 0.0

            # Determine trending strength
            if count >= 20:
                trend_strength = "high"
                base_score = 70
            elif count >= 10:
                trend_strength = "medium"
                base_score = 65
            else:
                trend_strength = "low"
                base_score = 60

            # Adjust score based on sentiment
            sentiment_boost = int(avg_sentiment * 10)  # -10 to +10 points
            final_score = max(45, min(80, base_score + sentiment_boost))

            trending_tickers.append({
                "symbol": ticker,
                "mention_count": count,
                "sentiment_score": round(avg_sentiment, 3),
                "trend_strength": trend_strength,
                "score": final_score,
                "type": "social_trending",
                "reason": f"Twitter trending: {count} mentions, sentiment {avg_sentiment:+.2f}"
            })

        # Sort by mention count and limit results
        trending_tickers.sort(key=lambda x: x["mention_count"], reverse=True)
        max_trending = int(_cfg("sentiment.max_trending_results", 10))

        return trending_tickers[:max_trending]

    def analyze_sentiment_for_watchlist(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Analyze Twitter sentiment for a list of symbols.
        Returns comprehensive sentiment data for each symbol.
        """
        results = {}

        for symbol in symbols:
            try:
                sentiment_data = self.get_twitter_sentiment_for_symbol(symbol)
                results[symbol] = sentiment_data
            except Exception as e:
                logger.error(f"Error analyzing sentiment for {symbol}: {e}")
                results[symbol] = {
                    "symbol": symbol,
                    "sentiment_score": 0.0,
                    "confidence": "low",
                    "error": str(e)
                }

        return results

    def get_sentiment_signals(self) -> List[Dict]:
        """
        Get sentiment-based trading signals.
        Looks for unusual sentiment patterns that could indicate opportunities.
        """
        signals = []

        # Get trending tickers first
        trending = self.get_trending_tickers()
        signals.extend(trending)

        # Analyze sentiment divergence patterns
        # (This could be enhanced with historical sentiment data)

        logger.info(f"Social sentiment analysis: {len(signals)} signals generated")

        return signals

    def cache_sentiment_data(self, data: Dict):
        """Cache sentiment analysis results for other modules to use."""
        try:
            cache_dir = Path(__file__).parent.parent / "state"
            cache_dir.mkdir(exist_ok=True)

            cache_file = cache_dir / "social_sentiment_cache.json"

            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "sentiment_data": data,
                "cache_duration_minutes": self.cache_duration
            }

            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)

        except Exception as e:
            logger.debug(f"Failed to cache sentiment data: {e}")


def analyze_social_sentiment(symbols: List[str] = None) -> List[Dict]:
    """
    Main entry point for social sentiment analysis.
    Returns trading signals based on social sentiment data.
    """
    analyzer = SocialSentimentAnalyzer()

    if symbols:
        # Analyze specific symbols
        sentiment_results = analyzer.analyze_sentiment_for_watchlist(symbols)

        # Convert to signals format
        signals = []
        for symbol, data in sentiment_results.items():
            if data.get("confidence") == "low":
                continue

            # Create trading signal based on sentiment
            base_score = 60
            sentiment_score = data.get("sentiment_score", 0.0)

            # Boost score for strong positive sentiment
            if sentiment_score > 0.3:
                base_score += 8
            elif sentiment_score > 0.1:
                base_score += 4
            elif sentiment_score < -0.3:
                base_score -= 5  # Contrarian play opportunity

            # Boost for high volume/trending
            if data.get("volume_rank") == "high":
                base_score += 5
            elif data.get("trending"):
                base_score += 3

            signals.append({
                "symbol": symbol,
                "score": max(45, min(base_score, 80)),
                "type": "social_sentiment",
                "reason": f"Social sentiment: {data.get('sentiment_label', 'neutral')} "
                         f"({data.get('tweet_count', 0)} tweets)",
                "sentiment_data": data
            })

        return signals

    else:
        # Get general trending signals
        return analyzer.get_sentiment_signals()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with some popular tickers
    test_symbols = ["AAPL", "TSLA", "NVDA", "SPY"]
    signals = analyze_social_sentiment(test_symbols)

    print(f"\n=== Social Sentiment Analysis Results ===")
    for signal in signals:
        print(f"{signal['symbol']:6s} | Score: {signal['score']:2d} | {signal['reason']}")

    # Test trending analysis
    trending_signals = analyze_social_sentiment()
    print(f"\n=== Trending Tickers ===")
    for signal in trending_signals:
        print(f"{signal['symbol']:6s} | {signal['mention_count']:3d} mentions | "
              f"Sentiment: {signal['sentiment_score']:+.2f}")