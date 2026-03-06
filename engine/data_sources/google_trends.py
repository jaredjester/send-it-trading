#!/usr/bin/env python3
"""
Google Trends Tracker
Monitors search interest for stock tickers and market keywords.
Free API, weekly data, can lead retail buying pressure.
"""

from pytrends.request import TrendReq
import pandas as pd
import json
from datetime import datetime, timedelta
import time
import os

class GoogleTrendsTracker:
    def __init__(self):
        """Initialize Google Trends API (no auth required)."""
        self.pytrends = TrendReq(hl='en-US', tz=360)
        
    def get_interest(self, keywords, timeframe='now 7-d', geo='US'):
        """
        Get search interest for keywords.
        
        Args:
            keywords: list of keywords (max 5 per request)
            timeframe: 'now 7-d', 'today 3-m', 'today 12-m', etc.
            geo: 'US', 'GB', etc.
        
        Returns:
            DataFrame with interest over time
        """
        try:
            self.pytrends.build_payload(keywords, timeframe=timeframe, geo=geo)
            interest = self.pytrends.interest_over_time()
            
            if not interest.empty:
                # Drop 'isPartial' column if present
                if 'isPartial' in interest.columns:
                    interest = interest.drop(columns=['isPartial'])
                
            return interest
        
        except Exception as e:
            print(f"⚠️  Google Trends API error: {e}")
            return pd.DataFrame()
    
    def get_related_queries(self, keyword):
        """Get related/rising search queries for a keyword."""
        try:
            self.pytrends.build_payload([keyword], timeframe='now 7-d')
            related = self.pytrends.related_queries()
            return related.get(keyword, {})
        except Exception as e:
            print(f"⚠️  Error getting related queries for {keyword}: {e}")
            return {}
    
    def scan_tickers(self, tickers, batch_size=5, delay=2):
        """
        Scan multiple tickers for search interest.
        
        Args:
            tickers: list of ticker symbols
            batch_size: max 5 per Google Trends API limit
            delay: seconds between requests (rate limiting)
        
        Returns:
            dict of {ticker: {interest_score, trend, ...}}
        """
        results = {}
        
        # Process in batches
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i+batch_size]
            print(f"  Fetching trends for {', '.join(batch)}...")
            
            interest = self.get_interest(batch, timeframe='now 7-d')
            
            if not interest.empty:
                for ticker in batch:
                    if ticker in interest.columns:
                        series = interest[ticker]
                        
                        # Calculate metrics
                        current = series.iloc[-1] if len(series) > 0 else 0
                        previous = series.iloc[0] if len(series) > 0 else 0
                        avg = series.mean()
                        max_val = series.max()
                        
                        # Trend direction
                        if len(series) > 1:
                            trend = 'rising' if current > previous else 'falling' if current < previous else 'flat'
                        else:
                            trend = 'unknown'
                        
                        # Spike detection (current > 2x average)
                        spike = current > (avg * 2) if avg > 0 else False
                        
                        results[ticker] = {
                            'interest_score': int(current),
                            'avg_7d': float(avg),
                            'max_7d': int(max_val),
                            'trend': trend,
                            'spike_detected': bool(spike),
                            'change_pct': float(((current - previous) / previous * 100) if previous > 0 else 0.0)
                        }
            
            # Rate limiting
            if i + batch_size < len(tickers):
                time.sleep(delay)
        
        return results
    
    def scan_market_keywords(self):
        """
        Scan general market sentiment keywords.
        Returns: dict with sentiment indicators.
        """
        keywords = ['stock market crash', 'buy stocks', 'stock market rally', 'recession', 'bull market']
        
        print(f"  Fetching market sentiment keywords...")
        interest = self.get_interest(keywords, timeframe='now 7-d')
        
        if interest.empty:
            return {}
        
        # Calculate market sentiment index
        bearish_score = interest['stock market crash'].mean() + interest['recession'].mean()
        bullish_score = interest['buy stocks'].mean() + interest['stock market rally'].mean() + interest['bull market'].mean()
        
        return {
            'bearish_keywords': float(bearish_score),
            'bullish_keywords': float(bullish_score),
            'net_sentiment': float(bullish_score - bearish_score),
            'timestamp': datetime.now().isoformat()
        }
    
    def run_daily_scan(self, watchlist, output_path='google_trends.json'):
        """
        Run full daily scan of watchlist tickers + market keywords.
        
        Args:
            watchlist: list of ticker symbols
            output_path: where to save results
        """
        print(f"🔍 Google Trends Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Scan tickers
        ticker_results = self.scan_tickers(watchlist)
        
        # Scan market keywords
        market_sentiment = self.scan_market_keywords()
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'tickers': ticker_results,
            'market_sentiment': market_sentiment,
            'total_tickers': len(ticker_results)
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        
        # Show top movers
        sorted_tickers = sorted(
            ticker_results.items(),
            key=lambda x: x[1]['interest_score'],
            reverse=True
        )
        
        print(f"📈 Top 10 by search interest:")
        for ticker, data in sorted_tickers[:10]:
            spike_flag = "🔥" if data['spike_detected'] else ""
            trend_emoji = "📈" if data['trend'] == 'rising' else "📉" if data['trend'] == 'falling' else "➡️"
            print(f"  {trend_emoji} {ticker}: {data['interest_score']} "
                  f"({data['change_pct']:+.1f}%) {spike_flag}")
        
        # Show market sentiment
        if market_sentiment:
            net = market_sentiment['net_sentiment']
            sentiment_emoji = "🟢" if net > 20 else "🔴" if net < -20 else "⚪"
            print(f"\n{sentiment_emoji} Market Sentiment: {net:+.1f} "
                  f"(Bullish: {market_sentiment['bullish_keywords']:.1f}, "
                  f"Bearish: {market_sentiment['bearish_keywords']:.1f})")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Google Trends Tracker')
    parser.add_argument('--tickers', nargs='+', default=['GME', 'TSLA', 'NVDA', 'AAPL', 'SPY'],
                        help='Tickers to track')
    parser.add_argument('--output', default='google_trends.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    tracker = GoogleTrendsTracker()
    tracker.run_daily_scan(args.tickers, output_path=args.output)

if __name__ == '__main__':
    main()
