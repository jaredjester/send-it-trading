#!/usr/bin/env python3
"""
Alt Data Dashboard Widget
Displays alt data signals on Pi TFT display.
Can be called from main dashboard.py to show recent signals.
"""

import json
import os
from datetime import datetime

class AltDataWidget:
    def __init__(self, data_dir='../data/alt_data'):
        self.data_dir = data_dir
        self.unified_file = os.path.join(data_dir, 'unified_signals.json')
    
    def load_signals(self):
        """Load latest unified signals."""
        if not os.path.exists(self.unified_file):
            return None
        
        try:
            with open(self.unified_file, 'r') as f:
                return json.load(f)
        except:
            return None
    
    def format_for_display(self, max_tickers=5):
        """
        Format alt data for TFT display.
        Returns list of strings ready to render.
        """
        signals = self.load_signals()
        
        if not signals:
            return ["Alt Data: No scan yet"]
        
        lines = []
        
        # Header with timestamp
        scan_time = signals.get('scan_date', 'Unknown')
        lines.append(f"ALT DATA ({scan_time})")
        
        # Macro regime
        regime = signals.get('macro_regime', 'neutral').upper()
        regime_emoji = "🟢" if regime == 'RISK_ON' else "🔴" if regime == 'RISK_OFF' else "⚪"
        lines.append(f"Regime: {regime_emoji} {regime}")
        
        # Top bullish/bearish signals
        tickers = signals.get('tickers', {})
        
        if not tickers:
            lines.append("No ticker data")
            return lines
        
        # Sort by composite score
        sorted_tickers = sorted(
            tickers.items(),
            key=lambda x: x[1]['composite_score'],
            reverse=True
        )
        
        # Top bullish
        lines.append("TOP BULLISH:")
        bullish = [t for t in sorted_tickers if t[1]['composite_score'] > 60][:max_tickers]
        
        if bullish:
            for ticker, data in bullish:
                score = data['composite_score']
                conf = data['confidence']
                lines.append(f"  {ticker}: {score:.0f} (conf {conf:.1f})")
        else:
            lines.append("  None")
        
        # Top bearish
        lines.append("TOP BEARISH:")
        bearish = [t for t in reversed(sorted_tickers) if t[1]['composite_score'] < 40][:max_tickers]
        
        if bearish:
            for ticker, data in bearish:
                score = data['composite_score']
                conf = data['confidence']
                lines.append(f"  {ticker}: {score:.0f} (conf {conf:.1f})")
        else:
            lines.append("  None")
        
        return lines
    
    def get_ticker_details(self, ticker):
        """Get detailed breakdown for a specific ticker."""
        signals = self.load_signals()
        
        if not signals or ticker not in signals.get('tickers', {}):
            return None
        
        data = signals['tickers'][ticker]
        
        return {
            'ticker': ticker,
            'composite_score': data['composite_score'],
            'confidence': data['confidence'],
            'reddit_sentiment': data.get('social_sentiment', 0),
            'stocktwits_sentiment': data.get('stocktwits_sentiment', 0),
            'search_interest': data.get('search_interest', 0),
            'search_trend': data.get('search_trend', 'flat'),
            'options_signal': data.get('options_signal', 'neutral'),
            'put_call_ratio': data.get('put_call_ratio')
        }
    
    def get_summary_line(self):
        """Get one-line summary for compact display."""
        signals = self.load_signals()
        
        if not signals:
            return "Alt: No data"
        
        summary = signals.get('summary', {})
        regime = signals.get('macro_regime', 'neutral')
        
        bullish = summary.get('bullish_signals', 0)
        bearish = summary.get('bearish_signals', 0)
        
        regime_icon = "🟢" if regime == 'risk_on' else "🔴" if regime == 'risk_off' else "⚪"
        
        return f"Alt: {regime_icon} {bullish}↑ {bearish}↓"

def main():
    """CLI entry point for testing."""
    widget = AltDataWidget()
    
    print("Alt Data Widget Test\n" + "=" * 40)
    
    # Summary line
    print(f"\nSummary: {widget.get_summary_line()}")
    
    # Full display
    print("\nFull Display:")
    lines = widget.format_for_display()
    for line in lines:
        print(line)
    
    # Example ticker details
    print("\nGME Details:")
    details = widget.get_ticker_details('GME')
    if details:
        for key, value in details.items():
            print(f"  {key}: {value}")
    else:
        print("  No data for GME")

if __name__ == '__main__':
    main()
