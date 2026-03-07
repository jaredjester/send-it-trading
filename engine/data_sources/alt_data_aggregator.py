#!/usr/bin/env python3
"""
Alternative Data Aggregator
Combines signals from Reddit, Google Trends, Options Flow, and FRED Macro.
Feeds unified signals into alpha_engine.py for trading decisions.
"""

import json
import os
from datetime import datetime
from collections import defaultdict

# Import our data sources
try:
    from .reddit_sentiment import RedditSentimentScraper
    from .google_trends import GoogleTrendsTracker
    from .options_flow import OptionsFlowTracker
    from .fred_macro import FREDMacroTracker
    try:
        from .stocktwits_sentiment import StockTwitsScraper
        HAS_STOCKTWITS = True
    except ImportError:
        HAS_STOCKTWITS = False
except ImportError:
    # Try absolute imports if relative imports fail
    try:
        from reddit_sentiment import RedditSentimentScraper
        from google_trends import GoogleTrendsTracker
        from options_flow import OptionsFlowTracker
        from fred_macro import FREDMacroTracker
        try:
            from stocktwits_sentiment import StockTwitsScraper
            HAS_STOCKTWITS = True
        except ImportError:
            HAS_STOCKTWITS = False
    except ImportError:
        print("⚠️  Data source modules not found. Make sure they're in the same directory.")
        HAS_STOCKTWITS = False

class AltDataAggregator:
    """
    Aggregates alternative data signals for trading strategy.
    """
    
    def __init__(self, data_dir='./data/alt_data'):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Initialize scrapers
        self.reddit = RedditSentimentScraper()
        self.trends = GoogleTrendsTracker()
        self.options = OptionsFlowTracker()
        self.macro = FREDMacroTracker()
        self.stocktwits = StockTwitsScraper() if HAS_STOCKTWITS else None
    
    def run_full_scan(self, watchlist):
        """
        Run all data sources and aggregate results.
        
        Args:
            watchlist: list of ticker symbols to monitor
        
        Returns:
            dict with unified signals
        """
        total_steps = 5 if self.stocktwits else 4
        print(f"\n🔄 ALT DATA SCAN - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        
        # 1. Reddit sentiment
        print(f"\n[1/{total_steps}] Reddit Sentiment...")
        reddit_file = os.path.join(self.data_dir, 'reddit_sentiment.json')
        reddit_data = self.reddit.run_daily_scan(output_path=reddit_file)
        
        # 2. Google Trends
        print(f"\n[2/{total_steps}] Google Trends...")
        trends_file = os.path.join(self.data_dir, 'google_trends.json')
        trends_data = self.trends.run_daily_scan(watchlist, output_path=trends_file)
        
        # 3. Options Flow
        print(f"\n[3/{total_steps}] Options Flow...")
        options_file = os.path.join(self.data_dir, 'options_flow.json')
        options_data = self.options.run_daily_scan(watchlist, output_path=options_file)
        
        # 4. FRED Macro
        print(f"\n[4/{total_steps}] FRED Macro Data...")
        macro_file = os.path.join(self.data_dir, 'fred_macro.json')
        macro_data = self.macro.run_daily_scan(output_path=macro_file)
        
        # 5. StockTwits (if available)
        stocktwits_data = {}
        if self.stocktwits:
            print(f"\n[5/{total_steps}] StockTwits Sentiment...")
            stocktwits_file = os.path.join(self.data_dir, 'stocktwits_sentiment.json')
            stocktwits_data = self.stocktwits.run_daily_scan(watchlist, output_path=stocktwits_file)
        
        # Aggregate all signals
        print("\n📊 Aggregating signals...")
        unified = self.aggregate_signals(watchlist, reddit_data, trends_data, options_data, macro_data, stocktwits_data)
        
        # Save unified signals
        unified_file = os.path.join(self.data_dir, 'unified_signals.json')
        with open(unified_file, 'w') as f:
            json.dump(unified, f, indent=2)
        
        print(f"\n✅ Unified signals saved to {unified_file}")
        print("=" * 60)
        
        return unified
    
    def aggregate_signals(self, watchlist, reddit_data, trends_data, options_data, macro_data, stocktwits_data=None):
        """
        Combine all data sources into unified signal format.
        
        Returns format compatible with alpha_engine.py:
        {
            'timestamp': ...,
            'macro_regime': 'risk_on' | 'risk_off' | 'neutral',
            'tickers': {
                'SYMBOL': {
                    'social_sentiment': -1 to +1,
                    'stocktwits_sentiment': -1 to +1,
                    'search_interest': 0-100,
                    'options_signal': 'bullish' | 'bearish' | 'neutral',
                    'composite_score': 0-100,
                    'confidence': 0-1
                }
            }
        }
        """
        unified = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'macro_regime': macro_data.get('macro_regime', 'neutral'),
            'tickers': {}
        }
        
        stocktwits_data = stocktwits_data or {}
        
        for ticker in watchlist:
            signals = {
                'ticker': ticker,
                'social_sentiment': 0.0,
                'stocktwits_sentiment': 0.0,
                'search_interest': 0,
                'search_trend': 'flat',
                'options_signal': 'neutral',
                'put_call_ratio': None,
                'composite_score': 50,  # Neutral baseline
                'confidence': 0.0,
                'signal_count': 0
            }
            
            # 1. Reddit sentiment
            if ticker in reddit_data.get('tickers', {}):
                reddit_ticker = reddit_data['tickers'][ticker]
                signals['social_sentiment'] = reddit_ticker['net_sentiment']
                signals['social_mentions'] = reddit_ticker['mentions']
                signals['confidence'] += reddit_ticker['confidence']
                signals['signal_count'] += 1
            
            # 2. Google Trends
            if ticker in trends_data.get('tickers', {}):
                trends_ticker = trends_data['tickers'][ticker]
                signals['search_interest'] = trends_ticker['interest_score']
                signals['search_trend'] = trends_ticker['trend']
                signals['search_spike'] = trends_ticker['spike_detected']
                if trends_ticker['interest_score'] > 0:
                    signals['signal_count'] += 1
                    signals['confidence'] += 0.3
            
            # 3. Options flow
            if ticker in options_data.get('tickers', {}):
                options_ticker = options_data['tickers'][ticker]
                signals['options_signal'] = options_ticker['interpretation']
                signals['put_call_ratio'] = options_ticker['put_call_ratio']
                signals['signal_count'] += 1
                signals['confidence'] += 0.4
            
            # 4. StockTwits sentiment
            if ticker in stocktwits_data.get('tickers', {}):
                stocktwits_ticker = stocktwits_data['tickers'][ticker]
                signals['stocktwits_sentiment'] = stocktwits_ticker['net_sentiment']
                signals['stocktwits_messages'] = stocktwits_ticker['total_messages']
                signals['confidence'] += stocktwits_ticker['confidence']
                signals['signal_count'] += 1
            
            # Calculate composite score (0-100)
            score = 50  # Start neutral
            
            # Social sentiment contribution (±15 points)
            score += signals['social_sentiment'] * 15
            
            # StockTwits sentiment contribution (±15 points)
            score += signals['stocktwits_sentiment'] * 15
            
            # Search interest contribution (±15 points)
            if signals['search_interest'] > 50:
                score += 15
            elif signals['search_interest'] < 20:
                score -= 15
            
            # Search trend contribution (±10 points)
            if signals['search_trend'] == 'rising':
                score += 10
            elif signals['search_trend'] == 'falling':
                score -= 10
            
            # Options signal contribution (±15 points)
            if signals['options_signal'] == 'bullish':
                score += 15
            elif signals['options_signal'] == 'bearish':
                score -= 15
            
            # Macro regime adjustment (±10 points)
            if unified['macro_regime'] == 'risk_on':
                score += 10
            elif unified['macro_regime'] == 'risk_off':
                score -= 10
            
            # Clamp to 0-100
            signals['composite_score'] = max(0, min(100, score))
            
            # Normalize confidence (0-1)
            if signals['signal_count'] > 0:
                signals['confidence'] = min(1.0, signals['confidence'] / signals['signal_count'])
            
            unified['tickers'][ticker] = signals
        
        # Add summary statistics
        unified['summary'] = self._generate_summary(unified)
        
        return unified
    
    def _generate_summary(self, unified):
        """Generate summary statistics for the scan."""
        tickers = unified['tickers']
        
        bullish = [t for t, d in tickers.items() if d['composite_score'] > 60]
        bearish = [t for t, d in tickers.items() if d['composite_score'] < 40]
        neutral = [t for t, d in tickers.items() if 40 <= d['composite_score'] <= 60]
        
        # High confidence signals
        high_conf = [t for t, d in tickers.items() if d['confidence'] > 0.6]
        
        return {
            'total_tickers': len(tickers),
            'bullish_signals': len(bullish),
            'bearish_signals': len(bearish),
            'neutral_signals': len(neutral),
            'high_confidence': len(high_conf),
            'macro_regime': unified['macro_regime'],
            'top_bullish': sorted(bullish, key=lambda t: tickers[t]['composite_score'], reverse=True)[:5],
            'top_bearish': sorted(bearish, key=lambda t: tickers[t]['composite_score'])[:5]
        }
    
    def get_signals_for_ticker(self, ticker):
        """
        Load latest unified signals for a specific ticker.
        Used by alpha_engine.py during trading cycle.
        """
        unified_file = os.path.join(self.data_dir, 'unified_signals.json')
        
        if not os.path.exists(unified_file):
            return None
        
        try:
            with open(unified_file, 'r') as f:
                data = json.load(f)
            
            return data.get('tickers', {}).get(ticker)
        except Exception as e:
            print(f"⚠️  Error loading signals for {ticker}: {e}")
            return None
    
    def get_macro_regime(self):
        """Get current macro regime for position sizing adjustments."""
        unified_file = os.path.join(self.data_dir, 'unified_signals.json')
        
        if not os.path.exists(unified_file):
            return 'neutral'
        
        try:
            with open(unified_file, 'r') as f:
                data = json.load(f)
            
            return data.get('macro_regime', 'neutral')
        except Exception:
            return 'neutral'

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Alternative Data Aggregator')
    parser.add_argument('--watchlist', nargs='+', 
                        default=['GME', 'SPY', 'TSLA', 'NVDA', 'AAPL', 'MSFT'],
                        help='Tickers to scan')
    parser.add_argument('--data-dir', default='../data/alt_data', 
                        help='Directory for output files')
    
    args = parser.parse_args()
    
    aggregator = AltDataAggregator(data_dir=args.data_dir)
    unified = aggregator.run_full_scan(args.watchlist)
    
    # Show summary
    summary = unified['summary']
    print(f"\n📈 SUMMARY:")
    print(f"  Macro Regime: {unified['macro_regime'].upper()}")
    print(f"  Bullish Signals: {summary['bullish_signals']}")
    print(f"  Bearish Signals: {summary['bearish_signals']}")
    print(f"  High Confidence: {summary['high_confidence']}")
    
    if summary['top_bullish']:
        print(f"\n🟢 Top Bullish: {', '.join(summary['top_bullish'])}")
    if summary['top_bearish']:
        print(f"🔴 Top Bearish: {', '.join(summary['top_bearish'])}")

if __name__ == '__main__':
    main()
