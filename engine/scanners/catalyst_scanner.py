"""
Catalyst Scanner

Detects unusual volume spikes (3x+ average) combined with fresh bullish news.
These setups offer 15-30% moves in hours when catalyst is material.

Entry: Immediately when catalyst confirmed + volume spike + price > VWAP
Exit: Trailing stop 7% OR news invalidated
"""
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
import logging

logger = logging.getLogger(__name__)


class CatalystScanner:
    """Scans for catalyst-driven volume spikes."""
    
    def __init__(self):
        self.alpaca_key = os.getenv('ALPACA_API_LIVE_KEY') or os.getenv('APCA_API_KEY_ID')
        self.alpaca_secret = os.getenv('ALPACA_API_SECRET') or os.getenv('APCA_API_SECRET_KEY')
        self.headers = {
            'APCA-API-KEY-ID': self.alpaca_key,
            'APCA-API-SECRET-KEY': self.alpaca_secret
        }
        
        # Universe to monitor (expand this)
        self.universe = self._get_active_universe()
    
    def _get_active_universe(self) -> List[str]:
        """Get actively traded stocks to monitor."""
        # Top 100 most active
        return [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD',
            'NFLX', 'DIS', 'PYPL', 'INTC', 'COIN', 'SQ', 'PLTR', 'SNAP',
            'UBER', 'LYFT', 'DASH', 'RBLX', 'ROKU', 'SHOP', 'SPOT', 'ZM',
            'GME', 'AMC', 'BBBY', 'SPCE', 'PLUG', 'RIOT', 'MARA', 'HOOD',
            'F', 'GM', 'NIO', 'LCID', 'RIVN', 'BA', 'CAT', 'DE',
            'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'V', 'MA',
            'XOM', 'CVX', 'COP', 'MRO', 'OXY', 'SLB', 'HAL', 'DVN'
        ]
    
    def scan_catalysts(self, min_volume_ratio: float = 3.0) -> List[Dict]:
        """
        Scan for catalyst opportunities.
        
        Args:
            min_volume_ratio: Minimum volume vs average (3.0 = 3x volume)
            
        Returns:
            List of catalyst plays with scores
        """
        catalysts = []
        
        for symbol in self.universe:
            try:
                catalyst_data = self._analyze_catalyst(symbol, min_volume_ratio)
                if catalyst_data:
                    catalysts.append(catalyst_data)
            except Exception as e:
                logger.debug(f"Catalyst scan failed for {symbol}: {e}")
        
        # Sort by score
        catalysts.sort(key=lambda x: x['score'], reverse=True)
        
        return catalysts
    
    def _analyze_catalyst(self, symbol: str, min_ratio: float) -> Optional[Dict]:
        """Analyze individual stock for catalyst opportunity."""
        
        # Check volume spike
        volume_data = self._get_volume_spike(symbol)
        if not volume_data or volume_data['ratio'] < min_ratio:
            return None
        
        # Check for catalyst (fresh news)
        catalyst = self._get_catalyst_data(symbol)
        if not catalyst or catalyst['score'] < 40:  # Minimum catalyst quality
            return None
        
        # Get price action
        price_data = self._get_price_action(symbol)
        if not price_data:
            return None
        
        # Calculate overall score
        score = self._score_catalyst(volume_data, catalyst, price_data)
        
        return {
            'symbol': symbol,
            'volume_ratio': volume_data['ratio'],
            'catalyst_type': catalyst['type'],
            'catalyst_score': catalyst['score'],
            'catalyst_age_hours': catalyst['age_hours'],
            'price': price_data['price'],
            'change_pct': price_data['change_pct'],
            'above_vwap': price_data['above_vwap'],
            'score': score,
            'timestamp': datetime.now().isoformat()
        }
    
    def _get_volume_spike(self, symbol: str) -> Optional[Dict]:
        """Detect volume spike."""
        try:
            end = datetime.now()
            start = end - timedelta(days=20)
            
            params = {
                'timeframe': '1Day',
                'start': start.strftime('%Y-%m-%dT00:00:00Z'),
                'end': end.strftime('%Y-%m-%dT00:00:00Z'),
                'feed': 'iex',
                'limit': 20
            }
            
            r = requests.get(
                f'https://data.alpaca.markets/v2/stocks/{symbol}/bars',
                headers=self.headers,
                params=params,
                timeout=5
            )
            
            if r.status_code != 200:
                return None
            
            data = r.json()
            if 'bars' not in data or len(data['bars']) < 10:
                return None
            
            # Average volume last 10 days
            volumes = [float(bar['v']) for bar in data['bars'][-11:-1]]  # Exclude today
            avg_volume = sum(volumes) / len(volumes)
            
            # Today's volume (so far)
            today_volume = float(data['bars'][-1]['v'])
            
            # Intraday adjustment (if before close, extrapolate)
            hour = datetime.now().hour
            if 9 <= hour < 16:  # Market hours
                hours_open = min(hour - 9.5, 6.5)  # Cap at full day
                if hours_open > 0:
                    today_volume = today_volume * (6.5 / hours_open)  # Extrapolate to full day
            
            ratio = today_volume / avg_volume if avg_volume > 0 else 0
            
            return {
                'avg_volume': avg_volume,
                'today_volume': today_volume,
                'ratio': ratio
            }
            
        except Exception as e:
            logger.debug(f"Volume spike check failed for {symbol}: {e}")
            return None
    
    def _get_catalyst_data(self, symbol: str) -> Optional[Dict]:
        """Get and score news catalyst."""
        try:
            end = datetime.now()
            start = end - timedelta(hours=12)  # Last 12 hours only (fresh news)
            
            params = {
                'start': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'end': end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'symbols': symbol,
                'limit': 10,
                'sort': 'desc'
            }
            
            r = requests.get(
                'https://data.alpaca.markets/v1beta1/news',
                headers=self.headers,
                params=params,
                timeout=5
            )
            
            if r.status_code != 200:
                return None
            
            data = r.json()
            if 'news' not in data or not data['news']:
                return None
            
            # Analyze most recent news
            best_catalyst = None
            best_score = 0
            
            for article in data['news'][:5]:
                headline = article.get('headline', '').lower()
                summary = article.get('summary', '').lower()
                text = headline + ' ' + summary
                
                catalyst_type, score = self._classify_catalyst(text)
                
                created = datetime.fromisoformat(article['created_at'].replace('Z', '+00:00'))
                age_hours = (datetime.now(created.tzinfo) - created).total_seconds() / 3600
                
                # Recency bonus (fresher = better)
                if age_hours < 1:
                    score += 20
                elif age_hours < 2:
                    score += 10
                
                if score > best_score:
                    best_score = score
                    best_catalyst = {
                        'type': catalyst_type,
                        'score': score,
                        'age_hours': age_hours,
                        'headline': article.get('headline', '')
                    }
            
            return best_catalyst
            
        except Exception as e:
            logger.debug(f"Catalyst check failed for {symbol}: {e}")
            return None
    
    def _classify_catalyst(self, text: str) -> tuple:
        """Classify catalyst type and score."""
        
        # High-impact catalysts (50+ points base)
        if any(kw in text for kw in ['acquisition', 'buyout', 'acquired', 'merge']):
            return ('ACQUISITION', 70)
        
        if any(kw in text for kw in ['fda approval', 'fda approved', 'drug approval']):
            return ('FDA_APPROVAL', 65)
        
        if any(kw in text for kw in ['earnings beat', 'beats estimates', 'revenue surprise']):
            return ('EARNINGS_BEAT', 60)
        
        # Medium-impact (30-50 points)
        if any(kw in text for kw in ['upgrade', 'raised target', 'price target increase']):
            return ('ANALYST_UPGRADE', 45)
        
        if any(kw in text for kw in ['partnership', 'deal signed', 'contract win']):
            return ('PARTNERSHIP', 40)
        
        if any(kw in text for kw in ['product launch', 'new product', 'breakthrough']):
            return ('PRODUCT', 40)
        
        # Lower-impact (20-30 points)
        if any(kw in text for kw in ['strong', 'positive', 'optimistic', 'bullish']):
            return ('POSITIVE_NEWS', 25)
        
        # Generic
        return ('GENERAL', 10)
    
    def _get_price_action(self, symbol: str) -> Optional[Dict]:
        """Get current price and momentum."""
        try:
            # Get latest trade
            r = requests.get(
                f'https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest',
                headers=self.headers,
                timeout=5
            )
            
            if r.status_code != 200:
                return None
            
            data = r.json()
            if 'trade' not in data:
                return None
            
            current_price = float(data['trade']['p'])
            
            # Get previous close for % change
            end = datetime.now()
            start = end - timedelta(days=2)
            
            params = {
                'timeframe': '1Day',
                'start': start.strftime('%Y-%m-%dT00:00:00Z'),
                'end': end.strftime('%Y-%m-%dT00:00:00Z'),
                'feed': 'iex',
                'limit': 2
            }
            
            r2 = requests.get(
                f'https://data.alpaca.markets/v2/stocks/{symbol}/bars',
                headers=self.headers,
                params=params,
                timeout=5
            )
            
            if r2.status_code == 200:
                bars = r2.json().get('bars', [])
                if bars:
                    prev_close = float(bars[-1]['c'])
                    change_pct = ((current_price / prev_close) - 1) * 100
                    
                    # Calculate VWAP (simplified - use today's bar)
                    vwap = (float(bars[-1]['h']) + float(bars[-1]['l']) + float(bars[-1]['c'])) / 3
                    above_vwap = current_price > vwap
                    
                    return {
                        'price': current_price,
                        'prev_close': prev_close,
                        'change_pct': change_pct,
                        'vwap': vwap,
                        'above_vwap': above_vwap
                    }
            
            return {
                'price': current_price,
                'change_pct': 0,
                'above_vwap': True
            }
            
        except Exception as e:
            logger.debug(f"Price action failed for {symbol}: {e}")
            return None
    
    def _score_catalyst(
        self,
        volume_data: Dict,
        catalyst: Dict,
        price_data: Dict
    ) -> float:
        """Score catalyst opportunity (0-100)."""
        score = 0.0
        
        # Catalyst quality (40 points max)
        score += catalyst['score'] * 0.40
        
        # Volume spike (30 points max)
        vol_ratio = volume_data['ratio']
        if vol_ratio >= 5.0:
            score += 30
        elif vol_ratio >= 4.0:
            score += 25
        elif vol_ratio >= 3.0:
            score += 20
        elif vol_ratio >= 2.0:
            score += 15
        
        # Price momentum (20 points max)
        change = price_data.get('change_pct', 0)
        if change > 10:
            score += 20
        elif change > 5:
            score += 15
        elif change > 2:
            score += 10
        elif change > 0:
            score += 5
        
        # Above VWAP (10 points)
        if price_data.get('above_vwap', False):
            score += 10
        
        return round(score, 1)
    
    def get_top_catalysts(self, limit: int = 5) -> List[Dict]:
        """Get top N catalyst plays."""
        all_catalysts = self.scan_catalysts()
        return all_catalysts[:limit]


def run_catalyst_scan():
    """Run catalyst scan (call from orchestrator)."""
    scanner = CatalystScanner()
    
    print("📰 Catalyst Scanner")
    print("=" * 60)
    
    catalysts = scanner.get_top_catalysts(limit=10)
    
    if not catalysts:
        print("No catalyst opportunities found (3x volume + news)")
        return []
    
    print(f"\nFound {len(catalysts)} catalyst plays:\n")
    
    for i, cat in enumerate(catalysts, 1):
        print(f"{i}. {cat['symbol']}")
        print(f"   Catalyst: {cat['catalyst_type']} (Score: {cat['catalyst_score']:.0f})")
        print(f"   Age: {cat['catalyst_age_hours']:.1f}h ago")
        print(f"   Volume: {cat['volume_ratio']:.1f}x average")
        print(f"   Price: ${cat['price']:.2f} ({cat['change_pct']:+.1f}%)")
        print(f"   Above VWAP: {'✅' if cat['above_vwap'] else '❌'}")
        print(f"   Overall Score: {cat['score']:.0f}/100")
        print()
    
    return catalysts


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=logging.INFO)
    
    catalysts = run_catalyst_scan()
    
    if catalysts:
        print(f"\n✅ Top catalyst: {catalysts[0]['symbol']} (Score: {catalysts[0]['score']:.0f})")
        print(f"   Type: {catalysts[0]['catalyst_type']}")
        print(f"   Entry: ${catalysts[0]['price']:.2f}")
        print(f"   Stop: ${catalysts[0]['price'] * 0.93:.2f} (-7%)")
