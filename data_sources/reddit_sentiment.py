#!/usr/bin/env python3
"""
Reddit Sentiment Scraper
Monitors r/wallstreetbets and r/stocks for ticker mentions and sentiment.
Case study: Hedge fund saw 15% accuracy lift using social data (PwC 2022).
"""

import praw
import re
import json
from datetime import datetime, timedelta
from collections import defaultdict
import os

# Sentiment keyword lists (bullish/bearish)
BULLISH_KEYWORDS = [
    'moon', 'rocket', 'bullish', 'calls', 'buy', 'long', 'pump', 'squeeze',
    'yolo', 'tendies', 'diamond hands', 'hold', 'hodl', 'breakout', 'rally',
    'surge', 'soaring', 'skyrocket', 'all-in', 'loading', 'accumulating'
]

BEARISH_KEYWORDS = [
    'crash', 'dump', 'bearish', 'puts', 'sell', 'short', 'tank', 'drill',
    'collapse', 'plunge', 'paper hands', 'rug pull', 'overvalued', 'bubble',
    'dead cat', 'falling knife', 'bagholding', 'exit', 'cut losses'
]

# Common stock ticker pattern (1-5 uppercase letters, not common words)
TICKER_PATTERN = re.compile(r'\b([A-Z]{1,5})\b')

# Filter out common false positives
EXCLUDED_WORDS = {
    'I', 'A', 'AM', 'PM', 'USA', 'CEO', 'ATH', 'DD', 'DD', 'YOLO', 'IMO',
    'FYI', 'TL', 'DR', 'TLDR', 'WSB', 'NYSE', 'NASDAQ', 'IPO', 'ETF',
    'GDP', 'CPI', 'FED', 'SEC', 'IRS', 'LLC', 'IT', 'AI', 'API', 'UI'
}

class RedditSentimentScraper:
    def __init__(self, client_id=None, client_secret=None, user_agent=None):
        """
        Initialize Reddit API client.
        If credentials not provided, uses read-only mode (no auth required for public data).
        """
        self.client_id = client_id or os.getenv('REDDIT_CLIENT_ID', 'not_needed_for_readonly')
        self.client_secret = client_secret or os.getenv('REDDIT_CLIENT_SECRET', 'not_needed_for_readonly')
        self.user_agent = user_agent or 'HedgeFundBot/1.0'
        
        # Initialize PRAW (Python Reddit API Wrapper)
        try:
            self.reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent
            )
        except Exception as e:
            print(f"⚠️  Reddit API init failed (will use read-only): {e}")
            self.reddit = None

    def extract_tickers(self, text):
        """Extract stock tickers from text."""
        tickers = set()
        matches = TICKER_PATTERN.findall(text.upper())
        for match in matches:
            if match not in EXCLUDED_WORDS and len(match) > 1:
                tickers.add(match)
        return list(tickers)

    def analyze_sentiment(self, text):
        """
        Analyze sentiment of text using keyword matching.
        Returns: (bullish_score, bearish_score)
        """
        text_lower = text.lower()
        
        bullish = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        bearish = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
        
        return bullish, bearish

    def scrape_subreddit(self, subreddit_name, limit=50, time_filter='day'):
        """
        Scrape top posts from a subreddit.
        Returns: list of {ticker, bullish, bearish, mentions, posts}
        """
        if not self.reddit:
            print(f"⚠️  Reddit API not initialized, skipping {subreddit_name}")
            return {}
        
        ticker_data = defaultdict(lambda: {
            'mentions': 0,
            'bullish': 0,
            'bearish': 0,
            'posts': []
        })
        
        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            
            # Get hot posts (top of the day)
            # Note: May hit rate limits without auth - fail gracefully
            for post in subreddit.hot(limit=limit):
                # Combine title and selftext
                text = f"{post.title} {post.selftext}"
                
                # Extract tickers
                tickers = self.extract_tickers(text)
                
                # Analyze sentiment
                bullish, bearish = self.analyze_sentiment(text)
                
                # Update ticker data
                for ticker in tickers:
                    ticker_data[ticker]['mentions'] += 1
                    ticker_data[ticker]['bullish'] += bullish
                    ticker_data[ticker]['bearish'] += bearish
                    ticker_data[ticker]['posts'].append({
                        'title': post.title[:100],
                        'score': post.score,
                        'url': f"https://reddit.com{post.permalink}",
                        'created': datetime.fromtimestamp(post.created_utc).isoformat()
                    })
        
        except Exception as e:
            error_msg = str(e)
            if '401' in error_msg or 'Unauthorized' in error_msg:
                print(f"⚠️  Reddit rate limited or needs auth for r/{subreddit_name}. Set REDDIT_CLIENT_ID/SECRET or wait.")
            else:
                print(f"⚠️  Error scraping r/{subreddit_name}: {e}")
        
        return dict(ticker_data)

    def aggregate_sentiment(self, subreddit_data_list):
        """
        Aggregate sentiment across multiple subreddits.
        Returns: {ticker: {net_sentiment, confidence, mention_count, ...}}
        """
        aggregated = defaultdict(lambda: {
            'mentions': 0,
            'bullish': 0,
            'bearish': 0,
            'net_sentiment': 0.0,
            'confidence': 0.0,
            'sources': []
        })
        
        for subreddit_name, ticker_data in subreddit_data_list:
            for ticker, data in ticker_data.items():
                aggregated[ticker]['mentions'] += data['mentions']
                aggregated[ticker]['bullish'] += data['bullish']
                aggregated[ticker]['bearish'] += data['bearish']
                aggregated[ticker]['sources'].append(subreddit_name)
        
        # Calculate net sentiment and confidence
        for ticker, data in aggregated.items():
            total = data['bullish'] + data['bearish']
            if total > 0:
                # Net sentiment: -1 (bearish) to +1 (bullish)
                data['net_sentiment'] = (data['bullish'] - data['bearish']) / total
                # Confidence: based on mention count (more mentions = higher confidence)
                data['confidence'] = min(1.0, data['mentions'] / 10.0)
            else:
                data['net_sentiment'] = 0.0
                data['confidence'] = 0.0
        
        return dict(aggregated)

    def run_daily_scan(self, output_path='reddit_sentiment.json'):
        """
        Run full daily scan of target subreddits.
        Saves results to JSON file.
        """
        print(f"📊 Reddit Sentiment Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        subreddits = ['wallstreetbets', 'stocks']
        results = []
        
        for sub in subreddits:
            print(f"  Scraping r/{sub}...")
            data = self.scrape_subreddit(sub, limit=50, time_filter='day')
            results.append((sub, data))
            print(f"    Found {len(data)} tickers with mentions")
        
        # Aggregate
        aggregated = self.aggregate_sentiment(results)
        
        # Sort by mention count
        sorted_tickers = sorted(
            aggregated.items(),
            key=lambda x: x[1]['mentions'],
            reverse=True
        )
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'subreddits': subreddits,
            'total_tickers': len(sorted_tickers),
            'tickers': {ticker: data for ticker, data in sorted_tickers[:50]}  # Top 50
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        print(f"📈 Top 10 by mentions:")
        for ticker, data in sorted_tickers[:10]:
            sentiment_label = "🟢" if data['net_sentiment'] > 0.3 else "🔴" if data['net_sentiment'] < -0.3 else "⚪"
            print(f"  {sentiment_label} {ticker}: {data['mentions']} mentions, "
                  f"sentiment {data['net_sentiment']:+.2f}, confidence {data['confidence']:.2f}")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Reddit Sentiment Scraper')
    parser.add_argument('--output', default='reddit_sentiment.json', help='Output JSON file')
    parser.add_argument('--client-id', help='Reddit API client ID (optional)')
    parser.add_argument('--client-secret', help='Reddit API client secret (optional)')
    
    args = parser.parse_args()
    
    scraper = RedditSentimentScraper(
        client_id=args.client_id,
        client_secret=args.client_secret
    )
    
    scraper.run_daily_scan(output_path=args.output)

if __name__ == '__main__':
    main()
