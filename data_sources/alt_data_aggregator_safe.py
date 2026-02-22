#!/usr/bin/env python3
"""
Alternative Data Aggregator (Safe Mode)
Combines signals from working data sources only.
Gracefully handles API failures and continues with available data.
"""

import json
import os
from datetime import datetime
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# Track which sources are working
WORKING_SOURCES = {
    'google_trends': True,  # No API key needed
    'options_flow': True,   # Uses Alpaca (we have keys)
    'reddit': False,        # Needs API keys (currently 401)
    'fred': False,          # Needs API key (currently 400)
    'stocktwits': True,     # Public scraping
    'pumpfun': False        # API unavailable
}


class AltDataAggregator:
    """
    Aggregates alternative data signals with graceful error handling.
    Skips broken APIs and continues with working sources.
    """
    
    def __init__(self, data_dir='./data/alt_data'):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Initialize only working scrapers
        self.sources = {}
        
        # Google Trends (no API key needed)
        if WORKING_SOURCES['google_trends']:
            try:
                from .google_trends import GoogleTrendsTracker
                self.sources['trends'] = GoogleTrendsTracker()
                logger.info("✓ Google Trends enabled")
            except Exception as e:
                logger.warning(f"Google Trends disabled: {e}")
                WORKING_SOURCES['google_trends'] = False
        
        # Options Flow (uses Alpaca keys)
        if WORKING_SOURCES['options_flow']:
            try:
                from .options_flow import OptionsFlowTracker
                self.sources['options'] = OptionsFlowTracker()
                logger.info("✓ Options Flow enabled")
            except Exception as e:
                logger.warning(f"Options Flow disabled: {e}")
                WORKING_SOURCES['options_flow'] = False
        
        # StockTwits (public scraping)
        if WORKING_SOURCES['stocktwits']:
            try:
                from .stocktwits_sentiment import StockTwitsScraper
                self.sources['stocktwits'] = StockTwitsScraper()
                logger.info("✓ StockTwits enabled")
            except Exception as e:
                logger.warning(f"StockTwits disabled: {e}")
                WORKING_SOURCES['stocktwits'] = False
        
        logger.info(f"Alt data initialized with {len(self.sources)} working sources")
    
    def run_full_scan(self, watchlist):
        """
        Run all working data sources and aggregate results.
        
        Args:
            watchlist: list of ticker symbols to monitor
        
        Returns:
            dict with unified signals
        """
        print(f"\n🔄 ALT DATA SCAN - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"Working sources: {len(self.sources)}")
        print("=" * 60)
        
        results = {
            'scan_time': datetime.now().isoformat(),
            'sources_used': list(self.sources.keys()),
            'sources_disabled': [k for k, v in WORKING_SOURCES.items() if not v],
            'tickers': {}
        }
        
        step = 1
        total = len(self.sources)
        
        # Google Trends
        if 'trends' in self.sources:
            print(f"\n[{step}/{total}] Google Trends...")
            try:
                trends_file = os.path.join(self.data_dir, 'google_trends.json')
                trends_data = self.sources['trends'].run_daily_scan(watchlist, output_path=trends_file)
                
                for ticker, data in trends_data.items():
                    if ticker not in results['tickers']:
                        results['tickers'][ticker] = {}
                    results['tickers'][ticker]['trends'] = data
                
                print(f"  ✓ {len(trends_data)} tickers analyzed")
            except Exception as e:
                logger.error(f"Google Trends failed: {e}")
                print(f"  ✗ Failed: {e}")
            step += 1
        
        # Options Flow
        if 'options' in self.sources:
            print(f"\n[{step}/{total}] Options Flow...")
            try:
                options_file = os.path.join(self.data_dir, 'options_flow.json')
                options_data = self.sources['options'].run_daily_scan(watchlist, output_path=options_file)
                
                for ticker, data in options_data.items():
                    if ticker not in results['tickers']:
                        results['tickers'][ticker] = {}
                    results['tickers'][ticker]['options'] = data
                
                print(f"  ✓ {len(options_data)} tickers analyzed")
            except Exception as e:
                logger.error(f"Options Flow failed: {e}")
                print(f"  ✗ Failed: {e}")
            step += 1
        
        # StockTwits
        if 'stocktwits' in self.sources:
            print(f"\n[{step}/{total}] StockTwits Sentiment...")
            try:
                stocktwits_file = os.path.join(self.data_dir, 'stocktwits_sentiment.json')
                stocktwits_data = self.sources['stocktwits'].run_daily_scan(watchlist, output_path=stocktwits_file)
                
                for ticker, data in stocktwits_data.items():
                    if ticker not in results['tickers']:
                        results['tickers'][ticker] = {}
                    results['tickers'][ticker]['stocktwits'] = data
                
                print(f"  ✓ {len(stocktwits_data)} tickers analyzed")
            except Exception as e:
                logger.error(f"StockTwits failed: {e}")
                print(f"  ✗ Failed: {e}")
            step += 1
        
        # Generate composite scores
        print(f"\n[{total}/{total}] Generating Composite Scores...")
        for ticker in results['tickers']:
            results['tickers'][ticker]['composite'] = self._calculate_composite(
                results['tickers'][ticker]
            )
        
        # Save unified results
        output_file = os.path.join(self.data_dir, 'unified_signals.json')
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"\n✅ Scan complete: {len(results['tickers'])} tickers")
        print(f"📁 Results: {output_file}")
        
        if results['sources_disabled']:
            print(f"\n⚠️  Disabled sources: {', '.join(results['sources_disabled'])}")
        
        return results
    
    def _calculate_composite(self, ticker_data):
        """Calculate composite score from available signals."""
        score = 50  # Neutral
        confidence = 0.0
        signals = []
        
        # Google Trends
        if 'trends' in ticker_data:
            trend = ticker_data['trends']
            if trend.get('is_spiking'):
                score += 10
                signals.append('trends_spike')
            confidence += 0.2
        
        # Options Flow
        if 'options' in ticker_data:
            options = ticker_data['options']
            pc_ratio = options.get('put_call_ratio', 1.0)
            if pc_ratio < 0.7:  # More calls = bullish
                score += 15
                signals.append('bullish_options')
            elif pc_ratio > 1.3:  # More puts = bearish
                score -= 15
                signals.append('bearish_options')
            confidence += 0.3
        
        # StockTwits
        if 'stocktwits' in ticker_data:
            st = ticker_data['stocktwits']
            sentiment = st.get('sentiment_score', 0)
            if sentiment > 0.6:
                score += 10
                signals.append('positive_social')
            elif sentiment < 0.4:
                score -= 10
                signals.append('negative_social')
            confidence += 0.2
        
        return {
            'score': max(0, min(100, score)),
            'confidence': confidence,
            'signals': signals,
            'timestamp': datetime.now().isoformat()
        }


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Test scan
    agg = AltDataAggregator()
    
    watchlist = ['SPY', 'GME', 'AAPL', 'NVDA', 'TSLA']
    results = agg.run_full_scan(watchlist)
    
    print("\n" + "=" * 60)
    print("COMPOSITE SCORES")
    print("=" * 60)
    
    for ticker, data in results['tickers'].items():
        comp = data.get('composite', {})
        print(f"\n{ticker}: {comp.get('score', 0):.0f}/100 "
              f"(confidence: {comp.get('confidence', 0):.1%})")
        if comp.get('signals'):
            print(f"  Signals: {', '.join(comp['signals'])}")
