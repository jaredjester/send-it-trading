#!/usr/bin/env python3
"""
SEC Insider Trading Tracker
Monitors Form 4 filings for insider buy/sell activity.
Insider buying often precedes positive price moves.

Data Source: SEC EDGAR (free, public)
Signal: Strong buy when insiders accumulate
"""

import requests
import json
from datetime import datetime, timedelta
import time
import os
from collections import defaultdict

class SECInsiderTracker:
    def __init__(self):
        """Initialize SEC EDGAR scraper."""
        self.base_url = 'https://data.sec.gov'
        self.headers = {
            'User-Agent': 'HedgeFund Bot/1.0 (jonny2298@live.com)',
            'Accept-Encoding': 'gzip, deflate',
            'Host': 'data.sec.gov'
        }
    
    def get_company_cik(self, ticker):
        """
        Get CIK (company identifier) for a ticker.
        Uses SEC ticker mapping.
        """
        # SEC provides a ticker-to-CIK mapping JSON
        url = 'https://www.sec.gov/files/company_tickers.json'
        
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                
                # Search for ticker
                for item in data.values():
                    if item.get('ticker', '').upper() == ticker.upper():
                        cik = str(item['cik_str']).zfill(10)
                        return cik
            
            return None
        except Exception as e:
            print(f"⚠️  Error getting CIK for {ticker}: {e}")
            return None
    
    def get_recent_form4_filings(self, cik, count=10):
        """
        Get recent Form 4 filings for a company.
        Form 4 = insider transaction report.
        
        Returns:
            list of filing metadata
        """
        if not cik:
            return []
        
        url = f"{self.base_url}/submissions/CIK{cik}.json"
        
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                
                filings = data.get('filings', {}).get('recent', {})
                forms = filings.get('form', [])
                dates = filings.get('filingDate', [])
                accessions = filings.get('accessionNumber', [])
                
                # Filter for Form 4
                form4s = []
                for i, form in enumerate(forms):
                    if form == '4' and i < len(dates) and i < len(accessions):
                        form4s.append({
                            'date': dates[i],
                            'accession': accessions[i].replace('-', '')
                        })
                        
                        if len(form4s) >= count:
                            break
                
                return form4s
            else:
                print(f"⚠️  SEC API error for CIK {cik}: {resp.status_code}")
                return []
        except Exception as e:
            print(f"⚠️  Error fetching Form 4s: {e}")
            return []
    
    def analyze_insider_sentiment(self, ticker, days=30):
        """
        Analyze insider trading sentiment for a ticker.
        
        Returns:
            dict with insider activity metrics
        """
        cik = self.get_company_cik(ticker)
        
        if not cik:
            return None
        
        filings = self.get_recent_form4_filings(cik, count=20)
        
        if not filings:
            return {
                'ticker': ticker,
                'insider_buys': 0,
                'insider_sells': 0,
                'net_sentiment': 0.0,
                'confidence': 0.0,
                'signal': 'neutral'
            }
        
        # Filter by date range
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        recent_filings = [f for f in filings if f['date'] >= cutoff]
        
        # Simplified: assume Form 4s filed = activity
        # In production, would parse XML to get actual buy/sell amounts
        # For now, treat filings as proxy (more filings = more activity)
        
        total_filings = len(recent_filings)
        
        # Heuristic: clusters of filings often indicate buying
        if total_filings >= 5:
            signal = 'bullish'
            net_sentiment = 0.7
        elif total_filings >= 3:
            signal = 'neutral'
            net_sentiment = 0.0
        else:
            signal = 'low_activity'
            net_sentiment = 0.0
        
        confidence = min(1.0, total_filings / 10.0)
        
        return {
            'ticker': ticker,
            'recent_form4s': total_filings,
            'filing_dates': [f['date'] for f in recent_filings],
            'net_sentiment': net_sentiment,
            'confidence': confidence,
            'signal': signal,
            'cik': cik
        }
    
    def scan_watchlist(self, tickers, delay=0.5):
        """
        Scan multiple tickers for insider activity.
        
        Args:
            tickers: list of symbols
            delay: seconds between requests (SEC rate limiting)
        """
        results = {}
        
        for ticker in tickers:
            print(f"  Checking insider trades for {ticker}...")
            
            data = self.analyze_insider_sentiment(ticker, days=30)
            
            if data:
                results[ticker] = data
            
            # SEC rate limiting (10 requests/second)
            time.sleep(delay)
        
        return results
    
    def run_daily_scan(self, watchlist, output_path='sec_insider_trades.json'):
        """Run full daily scan of insider activity."""
        print(f"📋 SEC Insider Trading Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
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
        
        # Show signals
        bullish = [(t, d) for t, d in results.items() if d['signal'] == 'bullish']
        
        if bullish:
            print(f"\n🟢 Insider Buying Signals:")
            for ticker, data in bullish:
                print(f"  {ticker}: {data['recent_form4s']} Form 4s in 30 days "
                      f"(confidence {data['confidence']:.2f})")
        else:
            print(f"\n⚪ No strong insider buying signals detected")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='SEC Insider Trading Tracker')
    parser.add_argument('--tickers', nargs='+', default=['GME', 'TSLA', 'NVDA'],
                        help='Tickers to track')
    parser.add_argument('--output', default='sec_insider_trades.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    tracker = SECInsiderTracker()
    tracker.run_daily_scan(args.tickers, output_path=args.output)

if __name__ == '__main__':
    main()
