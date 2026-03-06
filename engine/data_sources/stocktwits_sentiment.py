#!/usr/bin/env python3
"""
StockTwits Sentiment Scraper
Finance-specific social media platform with pure trading sentiment.
More signal, less noise than Reddit/Twitter.
"""

import requests
import json
from datetime import datetime
from collections import defaultdict
import time
import os

class StockTwitsScraper:
    def __init__(self):
        """Initialize StockTwits scraper (no auth required for public data)."""
        self.base_url = 'https://api.stocktwits.com/api/2'
        
    def get_stream(self, symbol, limit=30):
        """
        Get recent messages for a symbol.
        
        Args:
            symbol: ticker symbol
            limit: max messages to fetch (default 30, max 30)
        
        Returns:
            list of messages
        """
        url = f"{self.base_url}/streams/symbol/{symbol}.json"
        params = {'limit': min(limit, 30)}
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('messages', [])
            else:
                print(f"⚠️  StockTwits API error for {symbol}: {resp.status_code}")
                return []
        except Exception as e:
            print(f"⚠️  StockTwits exception for {symbol}: {e}")
            return []
    
    def analyze_message(self, message):
        """
        Extract sentiment from a StockTwits message.
        StockTwits provides labeled sentiment: bullish/bearish.
        
        Returns:
            ('bullish'|'bearish'|'neutral', confidence)
        """
        # StockTwits has built-in sentiment labels
        entities = message.get('entities', {})
        sentiment_data = entities.get('sentiment')
        
        if sentiment_data:
            basic = sentiment_data.get('basic')
            if basic == 'Bullish':
                return ('bullish', 1.0)
            elif basic == 'Bearish':
                return ('bearish', 1.0)
        
        return ('neutral', 0.0)
    
    def analyze_ticker(self, symbol):
        """
        Analyze sentiment for a specific ticker.
        
        Returns:
            dict with sentiment metrics
        """
        messages = self.get_stream(symbol, limit=30)
        
        if not messages:
            return None
        
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        total_impressions = 0
        
        for msg in messages:
            sentiment, confidence = self.analyze_message(msg)
            
            if sentiment == 'bullish':
                bullish_count += 1
            elif sentiment == 'bearish':
                bearish_count += 1
            else:
                neutral_count += 1
            
            # Track message reach (likes as proxy for impressions)
            total_impressions += msg.get('likes', {}).get('total', 0)
        
        total = len(messages)
        
        # Calculate net sentiment
        if total > 0:
            bullish_pct = bullish_count / total
            bearish_pct = bearish_count / total
            net_sentiment = (bullish_count - bearish_count) / total
        else:
            bullish_pct = bearish_pct = net_sentiment = 0
        
        # Confidence based on message count (more messages = higher confidence)
        confidence = min(1.0, total / 30.0)
        
        return {
            'symbol': symbol,
            'total_messages': total,
            'bullish': bullish_count,
            'bearish': bearish_count,
            'neutral': neutral_count,
            'bullish_pct': bullish_pct,
            'bearish_pct': bearish_pct,
            'net_sentiment': net_sentiment,
            'confidence': confidence,
            'total_reach': total_impressions,
            'interpretation': 'bullish' if net_sentiment > 0.2 else 'bearish' if net_sentiment < -0.2 else 'neutral'
        }
    
    def scan_watchlist(self, tickers, delay=2):
        """
        Scan multiple tickers.
        
        Args:
            tickers: list of symbols
            delay: seconds between requests (rate limiting)
        
        Returns:
            dict of {ticker: sentiment_data}
        """
        results = {}
        
        for ticker in tickers:
            print(f"  Analyzing StockTwits for {ticker}...")
            
            data = self.analyze_ticker(ticker)
            
            if data:
                results[ticker] = data
            
            # Rate limiting
            if ticker != tickers[-1]:
                time.sleep(delay)
        
        return results
    
    def run_daily_scan(self, watchlist, output_path='stocktwits_sentiment.json'):
        """
        Run full daily scan of watchlist.
        """
        print(f"📊 StockTwits Sentiment Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        results = self.scan_watchlist(watchlist)
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'tickers': results,
            'total_tickers': len(results)
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        
        # Show top signals
        sorted_tickers = sorted(
            results.items(),
            key=lambda x: abs(x[1]['net_sentiment']),
            reverse=True
        )
        
        print(f"📈 Top 10 by sentiment strength:")
        for ticker, data in sorted_tickers[:10]:
            emoji = "🟢" if data['net_sentiment'] > 0.2 else "🔴" if data['net_sentiment'] < -0.2 else "⚪"
            print(f"  {emoji} {ticker}: {data['net_sentiment']:+.2f} "
                  f"({data['bullish']}/{data['bearish']}/{data['neutral']} bull/bear/neutral, "
                  f"{data['total_messages']} msgs)")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='StockTwits Sentiment Scraper')
    parser.add_argument('--tickers', nargs='+', default=['GME', 'TSLA', 'SPY', 'NVDA'],
                        help='Tickers to track')
    parser.add_argument('--output', default='stocktwits_sentiment.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    scraper = StockTwitsScraper()
    scraper.run_daily_scan(args.tickers, output_path=args.output)

if __name__ == '__main__':
    main()
