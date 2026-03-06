"""
IC Integration - Connects decision logging to alpha tracking.

Records signal → outcome pairs for IC calculation.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .alpha_tracker import AlphaTracker
from .decision_logger import DecisionLogger

logger = logging.getLogger(__name__)


class ICIntegration:
    """
    Connects decision logs to alpha tracking for IC measurement.
    
    Flow:
    1. Decision logger records entry (signal context)
    2. Position closes (exit recorded)
    3. ICIntegration calculates forward returns
    4. Alpha tracker records signal → outcome
    5. IC calculated from many observations
    """
    
    def __init__(self, 
                 alpha_tracker: Optional[AlphaTracker] = None,
                 decision_logger: Optional[DecisionLogger] = None):
        self.alpha_tracker = alpha_tracker or AlphaTracker()
        self.decision_logger = decision_logger or DecisionLogger()
        
        # Track open trades
        self.open_trades_path = Path("evaluation/open_trades.json")
        self.open_trades = self._load_open_trades()
    
    def _load_open_trades(self) -> Dict:
        """Load open trades tracking."""
        if not self.open_trades_path.exists():
            return {}
        
        try:
            with open(self.open_trades_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load open trades: {e}")
            return {}
    
    def _save_open_trades(self):
        """Save open trades."""
        try:
            self.open_trades_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.open_trades_path, 'w') as f:
                json.dump(self.open_trades, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save open trades: {e}")
    
    def record_entry(
        self,
        symbol: str,
        entry_price: float,
        strategy: str,
        alpha_score: float,
        signal_details: Dict,
        quantity: int
    ):
        """
        Record trade entry with signal context.
        
        Args:
            symbol: Stock symbol
            entry_price: Entry price
            strategy: Strategy name (e.g., "mean_reversion")
            alpha_score: Alpha engine score (0-100)
            signal_details: Dict with RSI, volume, ADX, etc.
            quantity: Shares entered
        """
        trade_id = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        self.open_trades[trade_id] = {
            'symbol': symbol,
            'entry_price': entry_price,
            'entry_time': datetime.now().isoformat(),
            'strategy': strategy,
            'alpha_score': alpha_score,
            'signal_details': signal_details,
            'quantity': quantity,
            'status': 'OPEN'
        }
        
        self._save_open_trades()
        logger.info(f"Recorded entry: {trade_id} {symbol} @ ${entry_price:.2f}")
    
    def record_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        benchmark_return: float = 0.0
    ):
        """
        Record trade exit and calculate IC metrics.
        
        Args:
            symbol: Stock symbol
            exit_price: Exit price
            exit_reason: Why we exited
            benchmark_return: SPY return over same period
        """
        # Find matching open trade
        matching_trades = [
            (trade_id, trade) 
            for trade_id, trade in self.open_trades.items()
            if trade['symbol'] == symbol and trade['status'] == 'OPEN'
        ]
        
        if not matching_trades:
            logger.warning(f"No open trade found for {symbol} exit")
            return
        
        # Use oldest open trade (FIFO)
        trade_id, trade = matching_trades[0]
        
        # Calculate returns
        entry_price = trade['entry_price']
        pnl_pct = (exit_price - entry_price) / entry_price
        
        # Calculate time held
        entry_time = datetime.fromisoformat(trade['entry_time'])
        exit_time = datetime.now()
        days_held = (exit_time - entry_time).days
        
        # Record performance with alpha tracker
        strategy = trade['strategy']
        alpha_score = trade['alpha_score']
        
        # Normalize alpha score to -1 to +1 signal strength
        signal_strength = (alpha_score - 50) / 50  # 50 = neutral, 100 = +1, 0 = -1
        
        try:
            self.alpha_tracker.record_signal_performance(
                signal_name=strategy,
                signal_strength=signal_strength,
                forward_return_1d=pnl_pct,  # Approximate
                forward_return_5d=pnl_pct,  # Approximate
                benchmark_return_1d=benchmark_return
            )
            
            logger.info(
                f"IC recorded: {strategy} signal_strength={signal_strength:.2f} "
                f"return={pnl_pct:.1%} benchmark={benchmark_return:.1%}"
            )
        except Exception as e:
            logger.error(f"Failed to record IC for {symbol}: {e}")
        
        # Mark trade as closed
        trade['exit_price'] = exit_price
        trade['exit_time'] = exit_time.isoformat()
        trade['exit_reason'] = exit_reason
        trade['pnl_pct'] = pnl_pct
        trade['days_held'] = days_held
        trade['status'] = 'CLOSED'
        
        self._save_open_trades()
        
        logger.info(
            f"Recorded exit: {trade_id} {symbol} @ ${exit_price:.2f} "
            f"({pnl_pct:+.1%}, {days_held}d)"
        )
    
    def get_signal_quality(self, strategy: str) -> Dict:
        """
        Get IC metrics for a strategy.
        
        Returns:
            Dict with IC, hit rate, avg return
        """
        return self.alpha_tracker.get_signal_quality(strategy)
    
    def cleanup_old_trades(self, days: int = 90):
        """Clean up closed trades older than N days."""
        cutoff = datetime.now() - timedelta(days=days)
        
        cleaned = {}
        for trade_id, trade in self.open_trades.items():
            if trade['status'] == 'OPEN':
                cleaned[trade_id] = trade
            else:
                exit_time = datetime.fromisoformat(trade['exit_time'])
                if exit_time > cutoff:
                    cleaned[trade_id] = trade
        
        removed = len(self.open_trades) - len(cleaned)
        if removed > 0:
            logger.info(f"Cleaned {removed} old closed trades")
            self.open_trades = cleaned
            self._save_open_trades()


# Global instance
_ic_integration = None


def get_ic_integration() -> ICIntegration:
    """Get global IC integration instance."""
    global _ic_integration
    if _ic_integration is None:
        _ic_integration = ICIntegration()
    return _ic_integration


def record_trade_entry(symbol: str, entry_price: float, strategy: str, 
                       alpha_score: float, signal_details: Dict, quantity: int):
    """Helper to record trade entry."""
    ic = get_ic_integration()
    ic.record_entry(symbol, entry_price, strategy, alpha_score, signal_details, quantity)


def record_trade_exit(symbol: str, exit_price: float, exit_reason: str, 
                      benchmark_return: float = 0.0):
    """Helper to record trade exit."""
    ic = get_ic_integration()
    ic.record_exit(symbol, exit_price, exit_reason, benchmark_return)


if __name__ == '__main__':
    # Test IC integration
    logging.basicConfig(level=logging.INFO)
    
    ic = ICIntegration()
    
    # Simulate entry
    ic.record_entry(
        symbol='TEST',
        entry_price=100.0,
        strategy='mean_reversion',
        alpha_score=72,
        signal_details={'rsi': 28, 'volume_ratio': 2.1},
        quantity=10
    )
    
    print("✓ Entry recorded")
    
    # Simulate exit
    ic.record_exit(
        symbol='TEST',
        exit_price=108.0,
        exit_reason='take_profit',
        benchmark_return=0.02
    )
    
    print("✓ Exit recorded")
    
    # Check quality
    quality = ic.get_signal_quality('mean_reversion')
    print(f"\nSignal quality: {quality}")
