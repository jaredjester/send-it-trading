#!/usr/bin/env python3
"""
Profit Analytics & Performance Tracker
Real-time performance metrics for quantitative trading.

Tracks:
- Daily P&L and cumulative returns
- Alpha vs SPY benchmark
- Sharpe ratio, Sortino ratio
- Max drawdown
- Win rate and profit factor
- Strategy attribution (which strategies make money)
- Trade-by-trade analysis
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

class ProfitTracker:
    def __init__(self, data_dir='../data/analytics'):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.performance_file = os.path.join(data_dir, 'performance.json')
        self.trades_file = os.path.join(data_dir, 'trades.json')
        self.daily_snapshots_file = os.path.join(data_dir, 'daily_snapshots.json')
    
    def load_performance_history(self):
        """Load historical performance data."""
        if os.path.exists(self.performance_file):
            with open(self.performance_file, 'r') as f:
                return json.load(f)
        return {'daily': [], 'trades': []}
    
    def save_performance(self, data):
        """Save performance data."""
        with open(self.performance_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def record_daily_snapshot(self, portfolio_value, spy_price=None):
        """
        Record end-of-day portfolio value.
        
        Args:
            portfolio_value: total portfolio value
            spy_price: SPY close price for benchmark comparison
        """
        perf = self.load_performance_history()
        
        snapshot = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'timestamp': datetime.now().isoformat(),
            'portfolio_value': portfolio_value,
            'spy_price': spy_price
        }
        
        # Calculate daily return
        if perf['daily']:
            prev_value = perf['daily'][-1]['portfolio_value']
            daily_return = (portfolio_value - prev_value) / prev_value if prev_value > 0 else 0
            snapshot['daily_return'] = daily_return
            
            if spy_price and perf['daily'][-1].get('spy_price'):
                prev_spy = perf['daily'][-1]['spy_price']
                spy_return = (spy_price - prev_spy) / prev_spy if prev_spy > 0 else 0
                snapshot['spy_return'] = spy_return
                snapshot['alpha'] = daily_return - spy_return
        
        perf['daily'].append(snapshot)
        self.save_performance(perf)
        
        return snapshot
    
    def calculate_metrics(self, days=30):
        """
        Calculate performance metrics over recent period.
        
        Returns:
            dict with all key metrics
        """
        perf = self.load_performance_history()
        
        if not perf['daily'] or len(perf['daily']) < 2:
            return {'error': 'insufficient_data'}
        
        # Get recent data
        recent = perf['daily'][-days:] if len(perf['daily']) >= days else perf['daily']
        
        # Extract returns
        returns = [d['daily_return'] for d in recent if 'daily_return' in d]
        
        if not returns:
            return {'error': 'no_returns_data'}
        
        # Calculate metrics
        returns_array = np.array(returns)
        
        # Cumulative return
        cum_return = (1 + returns_array).prod() - 1
        
        # Annualized return (assume 252 trading days)
        trading_days = len(returns)
        annualized_return = (1 + cum_return) ** (252 / trading_days) - 1 if trading_days > 0 else 0
        
        # Volatility (annualized)
        volatility = returns_array.std() * np.sqrt(252) if len(returns_array) > 1 else 0
        
        # Sharpe ratio (assume 4% risk-free rate)
        risk_free_rate = 0.04
        sharpe = (annualized_return - risk_free_rate) / volatility if volatility > 0 else 0
        
        # Sortino ratio (downside deviation only)
        downside_returns = returns_array[returns_array < 0]
        downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 1 else volatility
        sortino = (annualized_return - risk_free_rate) / downside_vol if downside_vol > 0 else 0
        
        # Max drawdown
        cumulative = (1 + returns_array).cumprod()
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()
        
        # Win rate
        winning_days = (returns_array > 0).sum()
        win_rate = winning_days / len(returns_array) if len(returns_array) > 0 else 0
        
        # Profit factor (sum wins / sum losses)
        total_wins = returns_array[returns_array > 0].sum()
        total_losses = abs(returns_array[returns_array < 0].sum())
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        # Alpha vs SPY
        spy_returns = [d.get('alpha', 0) for d in recent if 'alpha' in d]
        avg_alpha = np.mean(spy_returns) if spy_returns else 0
        annualized_alpha = avg_alpha * 252
        
        return {
            'period_days': trading_days,
            'cumulative_return': cum_return,
            'annualized_return': annualized_return,
            'volatility': volatility,
            'sharpe_ratio': sharpe,
            'sortino_ratio': sortino,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_daily_alpha': avg_alpha,
            'annualized_alpha': annualized_alpha,
            'total_trading_days': len(perf['daily'])
        }
    
    def get_summary(self):
        """Get one-line performance summary."""
        metrics = self.calculate_metrics(days=30)
        
        if 'error' in metrics:
            return "Performance: Insufficient data"
        
        cum_ret = metrics['cumulative_return'] * 100
        sharpe = metrics['sharpe_ratio']
        win_rate = metrics['win_rate'] * 100
        
        return f"30d: {cum_ret:+.1f}% | Sharpe {sharpe:.2f} | Win {win_rate:.0f}%"
    
    def generate_report(self, days=30):
        """Generate detailed performance report."""
        metrics = self.calculate_metrics(days)
        
        if 'error' in metrics:
            return f"ERROR: {metrics['error']}"
        
        report = f"""
PERFORMANCE REPORT ({days} days)
{'='*50}

Returns:
  Cumulative:    {metrics['cumulative_return']*100:+.2f}%
  Annualized:    {metrics['annualized_return']*100:+.2f}%
  
Risk Metrics:
  Volatility:    {metrics['volatility']*100:.2f}%
  Max Drawdown:  {metrics['max_drawdown']*100:.2f}%
  Sharpe Ratio:  {metrics['sharpe_ratio']:.2f}
  Sortino Ratio: {metrics['sortino_ratio']:.2f}
  
Win Metrics:
  Win Rate:      {metrics['win_rate']*100:.1f}%
  Profit Factor: {metrics['profit_factor']:.2f}
  
Alpha vs SPY:
  Avg Daily:     {metrics['avg_daily_alpha']*100:+.3f}%
  Annualized:    {metrics['annualized_alpha']*100:+.2f}%
  
Trading Days: {metrics['period_days']} ({metrics['total_trading_days']} total)
"""
        return report

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Profit Tracker')
    parser.add_argument('--report', action='store_true', help='Generate performance report')
    parser.add_argument('--record', type=float, help='Record daily snapshot (portfolio value)')
    parser.add_argument('--spy', type=float, help='SPY price for benchmark')
    parser.add_argument('--days', type=int, default=30, help='Period for metrics')
    
    args = parser.parse_args()
    
    tracker = ProfitTracker()
    
    if args.record:
        snapshot = tracker.record_daily_snapshot(args.record, spy_price=args.spy)
        print(f"✅ Recorded snapshot: ${args.record:.2f}")
        if 'daily_return' in snapshot:
            print(f"   Daily return: {snapshot['daily_return']*100:+.2f}%")
        if 'alpha' in snapshot:
            print(f"   Alpha vs SPY: {snapshot['alpha']*100:+.2f}%")
    
    if args.report:
        print(tracker.generate_report(days=args.days))
    
    if not args.record and not args.report:
        print(tracker.get_summary())

if __name__ == '__main__':
    main()
