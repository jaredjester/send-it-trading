"""
Morning Gap & Go Scanner

Finds stocks gapping up 5%+ pre-market with volume and catalysts.
These setups often run 10-30% by midday.

Entry: 9:35 AM (after initial volatility settles)
Exit: Trailing stop 5% OR 11:00 AM
"""
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
import logging

logger = logging.getLogger(__name__)


class GapScanner:
    """Scans for morning gap-up opportunities."""
    
    def __init__(self):
        self.alpaca_key = os.getenv('ALPACA_API_LIVE_KEY') or os.getenv('APCA_API_KEY_ID')
        self.alpaca_secret = os.getenv('ALPACA_API_SECRET') or os.getenv('APCA_API_SECRET_KEY')
        self.headers = {
            'APCA-API-KEY-ID': self.alpaca_key,
            'APCA-API-SECRET-KEY': self.alpaca_secret
        }
        
        # Most active stocks to scan (top 200)
        self.universe = self._get_screener_universe()
    
    def _get_screener_universe(self) -> List[str]:
        """Get universe of stocks to scan."""
        # Start with most active stocks
        # TODO: Pull from Alpaca screener or custom list
        return [
            # Mega caps
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA',
            # Large caps
            'AMD', 'NFLX', 'DIS', 'PYPL', 'INTC', 'COIN', 'SQ',
            # Mid caps
            'PLTR', 'SNAP', 'UBER', 'LYFT', 'DASH', 'RBLX', 'ROKU',
            # High beta
            'GME', 'AMC', 'BBBY', 'SPCE', 'PLUG', 'RIOT', 'MARA',
            # ETFs
            'SPY', 'QQQ', 'IWM', 'XLF', 'XLE', 'XLK', 'XLV'
        ]
    
    def scan_gaps(self, min_gap_pct: float = 5.0) -> List[Dict]:
        """
        Scan for stocks gapping up pre-market.
        
        Args:
            min_gap_pct: Minimum gap percentage to qualify
            
        Returns:
            List of gap opportunities with scores
        """
        gaps = []
        
        for symbol in self.universe:
            try:
                gap_data = self._analyze_gap(symbol)
                if gap_data and gap_data['gap_pct'] >= min_gap_pct:
                    gaps.append(gap_data)
            except Exception as e:
                logger.debug(f"Gap scan failed for {symbol}: {e}")
        
        # Sort by score (descending)
        gaps.sort(key=lambda x: x['score'], reverse=True)
        
        return gaps
    
    def _analyze_gap(self, symbol: str) -> Optional[Dict]:
        """Analyze individual stock for gap opportunity."""
        
        # Get yesterday's close
        yesterday_close = self._get_previous_close(symbol)
        if not yesterday_close:
            return None
        
        # Get current pre-market price
        current_price = self._get_current_price(symbol)
        if not current_price:
            return None
        
        # Calculate gap
        gap_pct = ((current_price / yesterday_close) - 1) * 100
        
        if gap_pct < 0:  # Only gap-ups
            return None
        
        # Get volume data
        volume_data = self._get_volume_metrics(symbol)
        
        # Get news/catalyst
        news_score = self._check_catalyst(symbol)
        
        # Calculate overall score
        score = self._score_gap(gap_pct, volume_data, news_score, current_price)
        
        return {
            'symbol': symbol,
            'previous_close': yesterday_close,
            'current_price': current_price,
            'gap_pct': gap_pct,
            'volume_ratio': volume_data.get('ratio', 0),
            'news_score': news_score,
            'score': score,
            'timestamp': datetime.now().isoformat()
        }
    
    def _get_previous_close(self, symbol: str) -> Optional[float]:
        """Get yesterday's closing price."""
        try:
            end = datetime.now()
            start = end - timedelta(days=5)
            
            params = {
                'timeframe': '1Day',
                'start': start.strftime('%Y-%m-%dT00:00:00Z'),
                'end': end.strftime('%Y-%m-%dT00:00:00Z'),
                'feed': 'iex',
                'limit': 2
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
            if 'bars' not in data or not data['bars']:
                return None
            
            # Get most recent close
            return float(data['bars'][-1]['c'])
            
        except Exception as e:
            logger.debug(f"Failed to get prev close for {symbol}: {e}")
            return None
    
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price (latest trade or quote)."""
        try:
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
            
            return float(data['trade']['p'])
            
        except Exception as e:
            logger.debug(f"Failed to get current price for {symbol}: {e}")
            return None
    
    def _get_volume_metrics(self, symbol: str) -> Dict:
        """Get volume and calculate relative volume."""
        try:
            # Get recent volume history
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
                return {'ratio': 0}
            
            data = r.json()
            if 'bars' not in data or len(data['bars']) < 10:
                return {'ratio': 0}
            
            # Calculate average volume (last 10 days)
            volumes = [float(bar['v']) for bar in data['bars'][-10:]]
            avg_volume = sum(volumes) / len(volumes)
            
            # Get today's volume so far (pre-market)
            # Note: This is approximate - real impl would use intraday bars
            current_volume = volumes[-1] if volumes else 0
            
            ratio = current_volume / avg_volume if avg_volume > 0 else 0
            
            return {
                'avg_volume': avg_volume,
                'current_volume': current_volume,
                'ratio': ratio
            }
            
        except Exception as e:
            logger.debug(f"Failed to get volume for {symbol}: {e}")
            return {'ratio': 0}
    
    def _check_catalyst(self, symbol: str) -> float:
        """Check for news catalyst and score it."""
        try:
            # Get recent news
            end = datetime.now()
            start = end - timedelta(hours=24)
            
            params = {
                'start': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'end': end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'limit': 10,
                'sort': 'desc'
            }
            
            r = requests.get(
                f'https://data.alpaca.markets/v1beta1/news',
                headers=self.headers,
                params={**params, 'symbols': symbol},
                timeout=5
            )
            
            if r.status_code != 200:
                return 0.0
            
            data = r.json()
            if 'news' not in data or not data['news']:
                return 0.0
            
            # Score news based on keywords and recency
            score = 0.0
            for article in data['news'][:5]:  # Check top 5 articles
                headline = article.get('headline', '').lower()
                summary = article.get('summary', '').lower()
                text = headline + ' ' + summary
                
                # Bullish keywords
                if any(kw in text for kw in [
                    'acquisition', 'buyout', 'merger', 'beat',
                    'approval', 'fda', 'upgrade', 'strong',
                    'revenue', 'earnings surprise', 'breakthrough'
                ]):
                    score += 20
                
                # High-impact keywords
                if any(kw in text for kw in ['acquisition', 'merger', 'fda approval']):
                    score += 30
                
                # Recency bonus (fresh news = higher weight)
                created = datetime.fromisoformat(article['created_at'].replace('Z', '+00:00'))
                hours_old = (datetime.now(created.tzinfo) - created).total_seconds() / 3600
                if hours_old < 2:
                    score += 10
            
            return min(score, 100.0)  # Cap at 100
            
        except Exception as e:
            logger.debug(f"Failed to check catalyst for {symbol}: {e}")
            return 0.0
    
    def _score_gap(
        self,
        gap_pct: float,
        volume_data: Dict,
        news_score: float,
        current_price: float
    ) -> float:
        """
        Score gap opportunity (0-100).
        
        Factors:
        - Gap size (bigger = better, up to a point)
        - Volume ratio (higher = stronger)
        - News catalyst (catalyst-driven gaps more sustainable)
        - Price range (avoid penny stocks, prefer $5-$500)
        """
        score = 0.0
        
        # Gap size (30 points max)
        # Sweet spot: 5-15% (too big can reverse)
        if 5 <= gap_pct <= 10:
            score += 30
        elif 10 < gap_pct <= 15:
            score += 25
        elif 15 < gap_pct <= 25:
            score += 20
        elif gap_pct > 25:
            score += 10  # Too big, risky
        
        # Volume ratio (25 points max)
        vol_ratio = volume_data.get('ratio', 0)
        if vol_ratio >= 3.0:
            score += 25
        elif vol_ratio >= 2.0:
            score += 20
        elif vol_ratio >= 1.5:
            score += 15
        elif vol_ratio >= 1.0:
            score += 10
        
        # News catalyst (35 points max)
        score += news_score * 0.35
        
        # Price range (10 points max)
        if 10 <= current_price <= 200:
            score += 10
        elif 5 <= current_price < 10:
            score += 7
        elif 200 < current_price <= 500:
            score += 7
        else:
            score += 3  # Penny stock or too expensive
        
        return round(score, 1)
    
    def get_top_gaps(self, limit: int = 5) -> List[Dict]:
        """Get top N gap opportunities."""
        all_gaps = self.scan_gaps()
        return all_gaps[:limit]


def run_morning_scan():
    """Run morning gap scan (call from orchestrator)."""
    scanner = GapScanner()
    
    print("🔍 Morning Gap Scanner")
    print("=" * 60)
    
    gaps = scanner.get_top_gaps(limit=10)
    
    if not gaps:
        print("No gaps found meeting criteria (5%+ with volume)")
        return []
    
    print(f"\nFound {len(gaps)} gap opportunities:\n")
    
    for i, gap in enumerate(gaps, 1):
        print(f"{i}. {gap['symbol']}")
        print(f"   Gap: {gap['gap_pct']:+.1f}% (${gap['previous_close']:.2f} → ${gap['current_price']:.2f})")
        print(f"   Volume Ratio: {gap['volume_ratio']:.1f}x")
        print(f"   News Score: {gap['news_score']:.0f}/100")
        print(f"   Overall Score: {gap['score']:.0f}/100")
        print()
    
    return gaps


if __name__ == '__main__':
    # Test the scanner
    import logging
    logging.basicConfig(level=logging.INFO)
    
    gaps = run_morning_scan()
    
    if gaps:
        print(f"\n✅ Top gap: {gaps[0]['symbol']} (Score: {gaps[0]['score']:.0f})")
        print(f"   Entry: ${gaps[0]['current_price']:.2f}")
        print(f"   Stop: ${gaps[0]['current_price'] * 0.95:.2f} (-5%)")
        print(f"   Target: ${gaps[0]['current_price'] * 1.15:.2f} (+15%)")
