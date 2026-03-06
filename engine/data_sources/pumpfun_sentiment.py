#!/usr/bin/env python3
"""
pump.fun Sentiment Tracker
Monitors Solana meme token launch activity for crypto market risk appetite.
NOT for trading tokens - for sentiment gauge only.

Key Metrics:
- Launch velocity (tokens/hour) = mania indicator
- Graduation rate (% reaching Raydium) = quality gauge  
- Volume trends = ecosystem health
- Narrative themes = trend rotation

Use case: Correlate with SOL price for position sizing.
"""

import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import time
import os

class PumpFunSentimentTracker:
    def __init__(self):
        """
        Initialize pump.fun API tracker.
        Uses public endpoints - no auth required for basic data.
        """
        # Try multiple base URLs (pump.fun has several mirrors)
        self.base_urls = [
            'https://frontend-api.pump.fun',
            'https://pumpportal.fun/api',
            'https://api.pump.fun'
        ]
        self.active_base = None
    
    def _test_endpoint(self):
        """Test which base URL is working."""
        for base in self.base_urls:
            try:
                # Try to fetch recent coins
                resp = requests.get(f"{base}/coins", timeout=5)
                if resp.status_code == 200:
                    self.active_base = base
                    return True
            except Exception:
                continue
        return False
    
    def get_recent_launches(self, limit=100, offset=0):
        """
        Get recent token launches.
        
        Returns:
            list of launch data
        """
        if not self.active_base and not self._test_endpoint():
            print("⚠️  pump.fun API unavailable")
            return []
        
        try:
            url = f"{self.active_base}/coins"
            params = {'limit': limit, 'offset': offset, 'sort': 'created_timestamp', 'order': 'DESC'}
            
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get('coins', [])
            else:
                print(f"⚠️  pump.fun API error: {resp.status_code}")
                return []
        except Exception as e:
            print(f"⚠️  pump.fun exception: {e}")
            return []
    
    def calculate_launch_velocity(self, hours=24):
        """
        Calculate tokens launched per hour over recent period.
        High velocity = mania/FOMO.
        
        Returns:
            float: launches per hour
        """
        launches = self.get_recent_launches(limit=200)
        
        if not launches:
            return 0.0
        
        # Count launches in last N hours
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []
        
        for launch in launches:
            # Parse timestamp (assuming Unix timestamp in ms)
            try:
                ts = launch.get('created_timestamp', 0) / 1000
                launch_time = datetime.fromtimestamp(ts)
                if launch_time >= cutoff:
                    recent.append(launch)
            except Exception:
                continue
        
        velocity = len(recent) / hours if hours > 0 else 0
        return velocity
    
    def calculate_graduation_rate(self):
        """
        Calculate % of tokens that "graduate" to Raydium (real DEX).
        Higher rate = quality over spam.
        
        Returns:
            float: graduation percentage (0-100)
        """
        launches = self.get_recent_launches(limit=100)
        
        if not launches:
            return 0.0
        
        total = len(launches)
        graduated = 0
        
        for launch in launches:
            # Check if bonding curve completed or migrated to Raydium
            if launch.get('complete', False) or launch.get('raydium_pool'):
                graduated += 1
        
        rate = (graduated / total * 100) if total > 0 else 0
        return rate
    
    def analyze_volume_trends(self):
        """
        Analyze trading volume trends.
        
        Returns:
            dict with volume metrics
        """
        launches = self.get_recent_launches(limit=50)
        
        if not launches:
            return {'avg_volume': 0, 'trend': 'unknown'}
        
        volumes = []
        for launch in launches:
            vol = launch.get('volume_24h', 0) or launch.get('usd_market_cap', 0)
            if vol > 0:
                volumes.append(vol)
        
        if not volumes:
            return {'avg_volume': 0, 'trend': 'unknown'}
        
        avg_volume = sum(volumes) / len(volumes)
        
        # Compare first half vs second half
        mid = len(volumes) // 2
        first_half = sum(volumes[:mid]) / mid if mid > 0 else 0
        second_half = sum(volumes[mid:]) / (len(volumes) - mid) if len(volumes) > mid else 0
        
        if second_half > first_half * 1.2:
            trend = 'rising'
        elif second_half < first_half * 0.8:
            trend = 'falling'
        else:
            trend = 'flat'
        
        return {
            'avg_volume': avg_volume,
            'trend': trend,
            'recent_avg': second_half,
            'older_avg': first_half
        }
    
    def detect_narrative_themes(self):
        """
        Detect trending themes/memes from token names.
        
        Returns:
            dict of {theme: count}
        """
        launches = self.get_recent_launches(limit=100)
        
        if not launches:
            return {}
        
        # Common themes/keywords
        themes = defaultdict(int)
        
        theme_keywords = {
            'dog': ['dog', 'doge', 'shiba', 'puppy', 'woof'],
            'cat': ['cat', 'kitty', 'meow', 'purr'],
            'pepe': ['pepe', 'frog', 'kek'],
            'ai': ['ai', 'gpt', 'bot', 'agent'],
            'trump': ['trump', 'maga', 'donald'],
            'elon': ['elon', 'musk', 'tesla', 'spacex'],
            'wojak': ['wojak', 'feels', 'chad'],
            'inu': ['inu', 'shiba', 'akita']
        }
        
        for launch in launches:
            name = launch.get('name', '').lower()
            symbol = launch.get('symbol', '').lower()
            text = f"{name} {symbol}"
            
            for theme, keywords in theme_keywords.items():
                if any(kw in text for kw in keywords):
                    themes[theme] += 1
        
        return dict(themes)
    
    def calculate_risk_appetite_index(self):
        """
        Calculate composite risk appetite index (0-100).
        
        Components:
        - Launch velocity (higher = more risk-on)
        - Graduation rate (lower paradoxically = more degen)
        - Volume trend (rising = risk-on)
        
        Returns:
            dict with index and components
        """
        velocity = self.calculate_launch_velocity(hours=24)
        grad_rate = self.calculate_graduation_rate()
        volume = self.analyze_volume_trends()
        
        # Normalize components to 0-100
        # Launch velocity: 0-50 launches/hour → 0-100
        velocity_score = min(100, velocity * 2)
        
        # Graduation rate: inverse relationship (low grad = more degen = high risk)
        # 0% grad = 100, 50% grad = 0
        grad_score = max(0, 100 - (grad_rate * 2))
        
        # Volume trend: rising=100, flat=50, falling=0
        volume_score = {'rising': 100, 'flat': 50, 'falling': 0}.get(volume['trend'], 50)
        
        # Weighted average
        index = (velocity_score * 0.4 + grad_score * 0.3 + volume_score * 0.3)
        
        # Interpretation
        if index > 70:
            interpretation = 'high_risk_appetite'  # Degen mode
        elif index > 40:
            interpretation = 'moderate'
        else:
            interpretation = 'low_risk_appetite'  # Risk-off
        
        return {
            'risk_index': index,
            'interpretation': interpretation,
            'components': {
                'launch_velocity': velocity,
                'velocity_score': velocity_score,
                'graduation_rate': grad_rate,
                'grad_score': grad_score,
                'volume_trend': volume['trend'],
                'volume_score': volume_score
            }
        }
    
    def run_daily_scan(self, output_path='pumpfun_sentiment.json'):
        """
        Run full daily scan of pump.fun activity.
        """
        print(f"🚀 pump.fun Sentiment Scan - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Calculate all metrics
        risk_index = self.calculate_risk_appetite_index()
        themes = self.detect_narrative_themes()
        volume = self.analyze_volume_trends()
        
        # Format output
        output = {
            'timestamp': datetime.now().isoformat(),
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'risk_appetite_index': risk_index['risk_index'],
            'interpretation': risk_index['interpretation'],
            'launch_velocity': risk_index['components']['launch_velocity'],
            'graduation_rate': risk_index['components']['graduation_rate'],
            'volume_trend': volume['trend'],
            'avg_volume_24h': volume['avg_volume'],
            'trending_themes': themes,
            'components': risk_index['components']
        }
        
        # Save to file
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"✅ Saved to {output_path}")
        
        # Show summary
        index = risk_index['risk_index']
        interp = risk_index['interpretation'].replace('_', ' ').title()
        
        emoji = "🔥" if index > 70 else "🟢" if index > 40 else "🔵"
        
        print(f"\n{emoji} Risk Appetite Index: {index:.1f}/100 ({interp})")
        print(f"   Launch Velocity: {risk_index['components']['launch_velocity']:.1f} tokens/hour")
        print(f"   Graduation Rate: {risk_index['components']['graduation_rate']:.1f}%")
        print(f"   Volume Trend: {volume['trend'].upper()}")
        
        if themes:
            print(f"\n🎭 Trending Themes:")
            sorted_themes = sorted(themes.items(), key=lambda x: x[1], reverse=True)
            for theme, count in sorted_themes[:5]:
                print(f"   {theme}: {count} tokens")
        
        return output

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='pump.fun Sentiment Tracker')
    parser.add_argument('--output', default='pumpfun_sentiment.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    tracker = PumpFunSentimentTracker()
    tracker.run_daily_scan(output_path=args.output)

if __name__ == '__main__':
    main()
