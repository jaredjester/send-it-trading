#!/usr/bin/env python3
"""
Options Flow Tracker
Monitors put/call ratios, unusual volume, and IV for directional signals.
Uses Alpaca API (free tier access).
"""

import requests
import json
from datetime import datetime, timedelta
import os

class OptionsFlowTracker:
    def __init__(self, api_key=None, api_secret=None, base_url='https://data.alpaca.markets'):
        """Initialize Alpaca options data API."""
        self.api_key = api_key or os.getenv('ALPACA_API_LIVE_KEY')
        self.api_secret = api_secret or os.getenv('ALPACA_API_SECRET')
        self.base_url = base_url
        
        self.headers = {
            'APCA-API-KEY-ID': self.api_key,
            'APCA-API-SECRET-KEY': self.api_secret
        }
    
    def get_option_chain(self, symbol):
        """
        Get option chain for a symbol.
        Note: Alpaca free tier has limited options data.
        This is a placeholder - may need alternative source.
        """
        url = f"{self.base_url}/v2/options/contracts"
        params = {
            'underlying_symbol': symbol,
            'status': 'active'
        }
        
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"⚠️  Options API error for {symbol}: {resp.status_code}")
                return None
        except Exception as e:
            print(f"⚠️  Options API exception for {symbol}: {e}")
            return None
    
    def calculate_put_call_ratio(self, symbol, chain_data):
        """
        Calculate put/call ratio from option chain.
        Higher ratio = more bearish bets.
        """
        if not chain_data:
            return None
        
        puts = 0
        calls = 0
        
        for contract in chain_data.get('option_contracts', []):
            if contract['type'] == 'put':
                puts += contract.get('open_interest', 0)
            elif contract['type'] == 'call':
                calls += contract.get('open_interest', 0)
        
        if calls > 0:
            ratio = puts / calls
            return {
                'put_call_ratio': ratio,
                'total_puts': puts,
                'total_calls': calls,
                'interpretation': 'bearish' if ratio > 1.0 else 'bullish'
            }
        
        return None
    
    def get_vix_proxy(self):
        """
        Get VIX-like volatility proxy using SPY options.
        Returns implied volatility estimate.
        """
        # Simplified: just return a placeholder
        # Real implementation would calculate IV from SPY option prices
        return {
            'vix_proxy': 15.0,  # Placeholder
            'note': 'VIX proxy not fully implemented'
        }
    
    def scan_unusual_options_activity(self, tickers):
        """
        Scan for unusual options activity (volume spikes).
        
        Args:
            tickers: list of symbols to scan
        
        Returns:
            dict of {ticker: {unusual_calls, unusual_puts, ...}}
        """
        results = {}
        
        for ticker in tickers:
            print(f"  Checking options for {ticker}...")
            
            chain = self.get_option_chain(ticker)
            
            if chain:
                pc_ratio = self.calculate_put_call_ratio(ticker, chain)
                
                if pc_ratio:
                    results[ticker] = {
                        'put_call_ratio': pc_ratio['put_call_ratio'],
                        'interpretation': pc_ratio['interpretation'],
                        'total_open_interest': pc_ratio['total_puts'] + pc_ratio['total_calls']
                    }
        
        return results
    
    def run_daily_scan(self, watchlist, output_path='options_flow.json'):
        """
        Run full daily scan of options activity.
        
        Args:
            watchlist: list of ticker symbols
            output_path: where to save results
        """
        print(f"📊 Options Flow Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Check if we have valid API keys
        if not self.api_key or self.api_key == 'not_set':
            print("⚠️  Alpaca API keys not configured. Using mock data.")
            results = self._generate_mock_data(watchlist)
        else:
            results = self.scan_unusual_options_activity(watchlist)
        
        # Get VIX proxy
        vix = self.get_vix_proxy()
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'tickers': results,
            'market_volatility': vix,
            'total_tickers': len(results)
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        
        # Show interesting signals
        bearish_signals = [(t, d) for t, d in results.items() if d.get('interpretation') == 'bearish']
        bullish_signals = [(t, d) for t, d in results.items() if d.get('interpretation') == 'bullish']
        
        print(f"🔴 Bearish signals (high put/call): {len(bearish_signals)}")
        for ticker, data in bearish_signals[:5]:
            print(f"  {ticker}: P/C ratio {data['put_call_ratio']:.2f}")
        
        print(f"🟢 Bullish signals (low put/call): {len(bullish_signals)}")
        for ticker, data in bullish_signals[:5]:
            print(f"  {ticker}: P/C ratio {data['put_call_ratio']:.2f}")
        
        return output
    
    def _generate_mock_data(self, tickers):
        """Generate mock options data for testing."""
        import random
        results = {}
        for ticker in tickers:
            ratio = random.uniform(0.5, 1.5)
            results[ticker] = {
                'put_call_ratio': ratio,
                'interpretation': 'bearish' if ratio > 1.0 else 'bullish',
                'total_open_interest': random.randint(1000, 50000),
                'mock_data': True
            }
        return results

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Options Flow Tracker')
    parser.add_argument('--tickers', nargs='+', default=['GME', 'SPY', 'TSLA'],
                        help='Tickers to track')
    parser.add_argument('--output', default='options_flow.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    tracker = OptionsFlowTracker()
    tracker.run_daily_scan(args.tickers, output_path=args.output)

if __name__ == '__main__':
    main()
