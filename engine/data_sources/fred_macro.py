#!/usr/bin/env python3
"""
FRED Macro Data Tracker
Monitors key economic indicators from Federal Reserve Economic Data.
Free API, helps with regime detection (risk-on/off).
"""

import requests
import json
from datetime import datetime, timedelta
import os

class FREDMacroTracker:
    def __init__(self, api_key=None):
        """
        Initialize FRED API.
        Get free API key at: https://fred.stlouisfed.org/docs/api/api_key.html
        """
        self.api_key = api_key or os.getenv('FRED_API_KEY', 'demo')
        self.base_url = 'https://api.stlouisfed.org/fred'
    
    def get_series(self, series_id, observation_start=None):
        """
        Get time series data from FRED.
        
        Args:
            series_id: FRED series ID (e.g., 'GDP', 'UNRATE')
            observation_start: start date (YYYY-MM-DD)
        
        Returns:
            list of observations
        """
        url = f"{self.base_url}/series/observations"
        
        params = {
            'series_id': series_id,
            'api_key': self.api_key,
            'file_type': 'json'
        }
        
        if observation_start:
            params['observation_start'] = observation_start
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('observations', [])
            else:
                print(f"⚠️  FRED API error for {series_id}: {resp.status_code}")
                return []
        except Exception as e:
            print(f"⚠️  FRED API exception for {series_id}: {e}")
            return []
    
    def get_latest_value(self, series_id):
        """Get most recent value for a series."""
        # Get last 30 days
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        observations = self.get_series(series_id, observation_start=start_date)
        
        if observations:
            # Get last non-null value
            for obs in reversed(observations):
                if obs['value'] != '.':
                    try:
                        return {
                            'value': float(obs['value']),
                            'date': obs['date'],
                            'series_id': series_id
                        }
                    except ValueError:
                        continue
        
        return None
    
    def calculate_surprise(self, series_id, expected=None):
        """
        Calculate actual vs expected differential.
        If expected not provided, compares to previous period.
        """
        observations = self.get_series(series_id, observation_start=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
        
        if len(observations) < 2:
            return None
        
        # Filter out null values
        valid_obs = [obs for obs in observations if obs['value'] != '.']
        
        if len(valid_obs) < 2:
            return None
        
        latest = float(valid_obs[-1]['value'])
        previous = float(valid_obs[-2]['value'])
        
        change = latest - previous
        pct_change = (change / previous * 100) if previous != 0 else 0
        
        return {
            'latest': latest,
            'previous': previous,
            'change': change,
            'pct_change': pct_change,
            'date': valid_obs[-1]['date'],
            'direction': 'up' if change > 0 else 'down' if change < 0 else 'flat'
        }
    
    def detect_macro_regime(self):
        """
        Detect current macro regime using key indicators.
        Returns: 'risk_on', 'risk_off', 'neutral'
        """
        # Key indicators
        indicators = {
            'GDP': 'GDPC1',           # Real GDP (quarterly)
            'Unemployment': 'UNRATE', # Unemployment rate (monthly)
            'Inflation': 'CPIAUCSL',  # CPI (monthly)
            'Fed Rate': 'FEDFUNDS'    # Fed funds rate (monthly)
        }
        
        scores = []
        
        for name, series_id in indicators.items():
            latest = self.get_latest_value(series_id)
            if latest:
                # Simple heuristics (improve these based on historical norms)
                if name == 'GDP':
                    # Rising GDP = risk on
                    scores.append(1 if latest['value'] > 0 else -1)
                elif name == 'Unemployment':
                    # Falling unemployment = risk on
                    scores.append(-1 if latest['value'] < 5.0 else 1)
                elif name == 'Inflation':
                    # Moderate inflation (2-3%) = risk on
                    scores.append(0 if 2.0 <= latest['value'] <= 3.0 else -1)
                elif name == 'Fed Rate':
                    # Low rates = risk on
                    scores.append(-1 if latest['value'] < 2.0 else 1)
        
        if not scores:
            return 'neutral'
        
        avg_score = sum(scores) / len(scores)
        
        if avg_score > 0.3:
            return 'risk_on'
        elif avg_score < -0.3:
            return 'risk_off'
        else:
            return 'neutral'
    
    def run_daily_scan(self, output_path='fred_macro.json'):
        """
        Run full daily scan of macro indicators.
        """
        print(f"📊 FRED Macro Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Key indicators to track
        indicators = {
            'GDP': 'GDPC1',
            'Unemployment': 'UNRATE',
            'CPI': 'CPIAUCSL',
            'Fed_Funds_Rate': 'FEDFUNDS',
            'VIX': 'VIXCLS',
            'Treasury_10Y': 'DGS10',
            'Consumer_Sentiment': 'UMCSENT'
        }
        
        results = {}
        
        for name, series_id in indicators.items():
            print(f"  Fetching {name}...")
            
            latest = self.get_latest_value(series_id)
            surprise = self.calculate_surprise(series_id)
            
            if latest:
                results[name] = {
                    'value': latest['value'],
                    'date': latest['date'],
                    'series_id': series_id
                }
                
                if surprise:
                    results[name].update({
                        'previous': surprise['previous'],
                        'change': surprise['change'],
                        'pct_change': surprise['pct_change'],
                        'direction': surprise['direction']
                    })
        
        # Detect regime
        regime = self.detect_macro_regime()
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'indicators': results,
            'macro_regime': regime,
            'total_indicators': len(results)
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        
        # Show key indicators
        regime_emoji = "🟢" if regime == 'risk_on' else "🔴" if regime == 'risk_off' else "⚪"
        print(f"\n{regime_emoji} Macro Regime: {regime.upper()}")
        
        for name, data in results.items():
            if 'direction' in data:
                direction_emoji = "📈" if data['direction'] == 'up' else "📉" if data['direction'] == 'down' else "➡️"
                print(f"  {direction_emoji} {name}: {data['value']:.2f} "
                      f"({data['pct_change']:+.1f}% from previous)")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='FRED Macro Data Tracker')
    parser.add_argument('--output', default='fred_macro.json', help='Output JSON file')
    parser.add_argument('--api-key', help='FRED API key (or set FRED_API_KEY env var)')
    
    args = parser.parse_args()
    
    tracker = FREDMacroTracker(api_key=args.api_key)
    tracker.run_daily_scan(output_path=args.output)

if __name__ == '__main__':
    main()
