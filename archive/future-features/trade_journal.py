"""
trade_journal.py - Complete Trade Audit Trail and Performance Tracking

Records every trade decision with full context for audit, learning, and compliance.
Tracks entries, exits, skips, and generates performance reports.

Author: Risk Fortress System
Date: 2026-02-17
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Complete Trade Audit Trail System
    
    Records:
    - Entry decisions (why we bought, what signals triggered)
    - Exit decisions (why we sold, P&L, hold duration)
    - Skip decisions (why we didn't trade - crucial for learning)
    
    Generates:
    - Daily summaries
    - Performance reports (win rate, Sharpe, drawdown)
    - Strategy effectiveness metrics
    """
    
    def __init__(self, journal_file: str):
        """
        Initialize Trade Journal.
        
        Args:
            journal_file: Path to JSON file for persisting trade history
        """
        self.journal_file = journal_file
        self.trades = []  # List of all trade records
        self.load_journal()
    
    def load_journal(self):
        """Load trade history from disk."""
        try:
            if os.path.exists(self.journal_file):
                with open(self.journal_file, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get('trades', [])
                    logger.info(f"Trade journal loaded: {len(self.trades)} historical trades")
            else:
                logger.info("Trade journal initialized with empty history")
                self.trades = []
        except Exception as e:
            logger.error(f"Failed to load trade journal: {e}", exc_info=True)
            self.trades = []
    
    def save_journal(self):
        """Persist trade history to disk."""
        try:
            os.makedirs(os.path.dirname(self.journal_file), exist_ok=True)
            with open(self.journal_file, 'w') as f:
                json.dump({'trades': self.trades}, f, indent=2)
            logger.debug(f"Trade journal saved: {len(self.trades)} trades")
        except Exception as e:
            logger.error(f"Failed to save trade journal: {e}", exc_info=True)
    
    def record_entry(
        self,
        symbol: str,
        price: float,
        qty: int,
        signals: Dict,
        risk_check: Dict,
        confidence: float,
        strategy: str
    ):
        """
        Record a trade entry with full context.
        
        Args:
            symbol: Stock ticker
            price: Entry price
            qty: Quantity purchased
            signals: Dict of signals that triggered (e.g., {'rsi': 30, 'macd': 'bullish'})
            risk_check: Risk management check results
            confidence: Model confidence score (0-1)
            strategy: Strategy name (e.g., 'momentum', 'mean_reversion')
        """
        try:
            entry = {
                'type': 'entry',
                'symbol': symbol.upper(),
                'timestamp': datetime.now().isoformat(),
                'date': datetime.now().strftime('%Y-%m-%d'),
                'time': datetime.now().strftime('%H:%M:%S'),
                'price': round(price, 2),
                'qty': qty,
                'dollar_amount': round(price * qty, 2),
                'signals': signals,
                'risk_check': risk_check,
                'confidence': round(confidence, 3),
                'strategy': strategy,
                'exit_price': None,
                'exit_timestamp': None,
                'pnl': None,
                'pnl_pct': None,
                'hold_days': None,
                'exit_reason': None
            }
            
            self.trades.append(entry)
            self.save_journal()
            
            logger.info(f"ENTRY RECORDED: {symbol} {qty}@${price:.2f} ({strategy}, conf={confidence:.2f})")
            logger.debug(f"  Signals: {signals}")
            logger.debug(f"  Risk check: {risk_check}")
            
        except Exception as e:
            logger.error(f"Failed to record entry: {e}", exc_info=True)
    
    def record_exit(
        self,
        symbol: str,
        price: float,
        qty: int,
        reason: str,
        pnl: float,
        hold_days: int
    ):
        """
        Record a trade exit.
        
        Args:
            symbol: Stock ticker
            price: Exit price
            qty: Quantity sold
            reason: Exit reason (e.g., 'stop_loss', 'take_profit', 'time_decay')
            pnl: Profit/loss in dollars
            hold_days: Number of days held
        """
        try:
            # Find the most recent entry for this symbol without an exit
            entry = None
            for trade in reversed(self.trades):
                if (trade['symbol'] == symbol.upper() and 
                    trade['type'] == 'entry' and 
                    trade.get('exit_price') is None):
                    entry = trade
                    break
            
            if entry:
                # Update the entry record with exit info
                entry['exit_price'] = round(price, 2)
                entry['exit_timestamp'] = datetime.now().isoformat()
                entry['exit_date'] = datetime.now().strftime('%Y-%m-%d')
                entry['exit_time'] = datetime.now().strftime('%H:%M:%S')
                entry['exit_reason'] = reason
                entry['pnl'] = round(pnl, 2)
                
                if entry['dollar_amount'] > 0:
                    entry['pnl_pct'] = round((pnl / entry['dollar_amount']) * 100, 2)
                else:
                    entry['pnl_pct'] = 0.0
                
                entry['hold_days'] = hold_days
                
                self.save_journal()
                
                win = pnl > 0
                status = "WIN" if win else "LOSS"
                logger.info(f"EXIT RECORDED: {symbol} {qty}@${price:.2f} - {status} ${pnl:.2f} ({entry['pnl_pct']:.2f}%) in {hold_days} days")
                logger.debug(f"  Reason: {reason}")
            else:
                # No matching entry found - create standalone exit record
                logger.warning(f"Exit without entry: {symbol} {qty}@${price:.2f}")
                exit_record = {
                    'type': 'exit',
                    'symbol': symbol.upper(),
                    'timestamp': datetime.now().isoformat(),
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'price': round(price, 2),
                    'qty': qty,
                    'reason': reason,
                    'pnl': round(pnl, 2),
                    'hold_days': hold_days,
                    'note': 'Exit without matching entry'
                }
                self.trades.append(exit_record)
                self.save_journal()
            
        except Exception as e:
            logger.error(f"Failed to record exit: {e}", exc_info=True)
    
    def record_skip(self, symbol: str, reason: str, signals: Dict):
        """
        Record a skipped trade opportunity.
        Crucial for learning - understanding why we DIDN'T trade.
        
        Args:
            symbol: Stock ticker
            reason: Why we skipped (e.g., 'risk_limit', 'pdt_block', 'low_confidence')
            signals: What signals were present
        """
        try:
            skip = {
                'type': 'skip',
                'symbol': symbol.upper(),
                'timestamp': datetime.now().isoformat(),
                'date': datetime.now().strftime('%Y-%m-%d'),
                'time': datetime.now().strftime('%H:%M:%S'),
                'reason': reason,
                'signals': signals
            }
            
            self.trades.append(skip)
            self.save_journal()
            
            logger.info(f"SKIP RECORDED: {symbol} - {reason}")
            logger.debug(f"  Signals present: {signals}")
            
        except Exception as e:
            logger.error(f"Failed to record skip: {e}", exc_info=True)
    
    def daily_summary(self, date: Optional[str] = None) -> Dict:
        """
        Generate summary of today's trading activity.
        
        Args:
            date: Date string (YYYY-MM-DD), defaults to today
        
        Returns:
            Dict with daily statistics
        """
        try:
            if date is None:
                date = datetime.now().strftime('%Y-%m-%d')
            
            # Filter trades for this date
            daily_trades = [t for t in self.trades if t.get('date') == date]
            
            entries = [t for t in daily_trades if t['type'] == 'entry']
            exits = [t for t in daily_trades if t['type'] == 'exit' or t.get('exit_price') is not None]
            skips = [t for t in daily_trades if t['type'] == 'skip']
            
            # Calculate P&L from completed trades
            completed_exits = [t for t in exits if t.get('pnl') is not None]
            total_pnl = sum(t.get('pnl', 0.0) for t in completed_exits)
            wins = [t for t in completed_exits if t.get('pnl', 0.0) > 0]
            losses = [t for t in completed_exits if t.get('pnl', 0.0) < 0]
            
            win_rate = len(wins) / len(completed_exits) if completed_exits else 0.0
            
            summary = {
                'date': date,
                'total_trades': len(entries),
                'exits': len(completed_exits),
                'skips': len(skips),
                'total_pnl': round(total_pnl, 2),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': round(win_rate * 100, 1),
                'avg_win': round(np.mean([t['pnl'] for t in wins]), 2) if wins else 0.0,
                'avg_loss': round(np.mean([t['pnl'] for t in losses]), 2) if losses else 0.0,
                'symbols_traded': list(set(t['symbol'] for t in entries)),
                'skip_reasons': {}
            }
            
            # Aggregate skip reasons
            for skip in skips:
                reason = skip.get('reason', 'unknown')
                summary['skip_reasons'][reason] = summary['skip_reasons'].get(reason, 0) + 1
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to generate daily summary: {e}", exc_info=True)
            return {'date': date, 'error': str(e)}
    
    def get_performance_report(self, days: int = 30) -> Dict:
        """
        Generate comprehensive performance report.
        
        Args:
            days: Number of days to analyze
        
        Returns:
            Dict with performance metrics
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            # Filter recent trades
            recent_trades = [
                t for t in self.trades
                if t.get('date', '9999-99-99') >= cutoff_date and t.get('exit_price') is not None
            ]
            
            if not recent_trades:
                logger.warning(f"No completed trades in last {days} days")
                return {
                    'days': days,
                    'total_trades': 0,
                    'note': 'No completed trades in period'
                }
            
            # Calculate basic metrics
            completed = [t for t in recent_trades if t.get('pnl') is not None]
            total_pnl = sum(t.get('pnl', 0.0) for t in completed)
            
            wins = [t for t in completed if t.get('pnl', 0.0) > 0]
            losses = [t for t in completed if t.get('pnl', 0.0) < 0]
            
            win_rate = len(wins) / len(completed) if completed else 0.0
            
            avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0.0
            avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0.0
            
            # Profit factor
            total_wins = sum(t['pnl'] for t in wins) if wins else 0.0
            total_losses = abs(sum(t['pnl'] for t in losses)) if losses else 0.0
            profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
            
            # Average hold time
            hold_times = [t.get('hold_days', 0) for t in completed if t.get('hold_days') is not None]
            avg_hold_days = np.mean(hold_times) if hold_times else 0.0
            
            # Sharpe ratio (simplified - assumes daily returns)
            pnl_values = [t.get('pnl', 0.0) for t in completed]
            if len(pnl_values) > 1:
                sharpe = np.mean(pnl_values) / np.std(pnl_values) if np.std(pnl_values) > 0 else 0.0
                sharpe = sharpe * np.sqrt(252)  # Annualize
            else:
                sharpe = 0.0
            
            # Max drawdown
            cumulative_pnl = np.cumsum([t.get('pnl', 0.0) for t in completed])
            running_max = np.maximum.accumulate(cumulative_pnl)
            drawdown = running_max - cumulative_pnl
            max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0.0
            
            # Strategy breakdown
            strategy_stats = {}
            for trade in completed:
                strategy = trade.get('strategy', 'unknown')
                if strategy not in strategy_stats:
                    strategy_stats[strategy] = {'trades': 0, 'pnl': 0.0, 'wins': 0}
                
                strategy_stats[strategy]['trades'] += 1
                strategy_stats[strategy]['pnl'] += trade.get('pnl', 0.0)
                if trade.get('pnl', 0.0) > 0:
                    strategy_stats[strategy]['wins'] += 1
            
            # Calculate win rate per strategy
            for strategy, stats in strategy_stats.items():
                stats['win_rate'] = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0.0
                stats['pnl'] = round(stats['pnl'], 2)
                stats['win_rate'] = round(stats['win_rate'], 1)
            
            report = {
                'period_days': days,
                'total_trades': len(completed),
                'total_pnl': round(total_pnl, 2),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': round(win_rate * 100, 1),
                'avg_win': round(avg_win, 2),
                'avg_loss': round(avg_loss, 2),
                'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 'inf',
                'sharpe_ratio': round(sharpe, 2),
                'max_drawdown': round(max_drawdown, 2),
                'avg_hold_days': round(avg_hold_days, 1),
                'best_trade': round(max([t.get('pnl', 0.0) for t in completed]), 2) if completed else 0.0,
                'worst_trade': round(min([t.get('pnl', 0.0) for t in completed]), 2) if completed else 0.0,
                'strategy_breakdown': strategy_stats,
                'most_traded_symbols': self._get_most_traded(completed, top_n=5)
            }
            
            return report
            
        except Exception as e:
            logger.error(f"Failed to generate performance report: {e}", exc_info=True)
            return {'error': str(e)}
    
    def _get_most_traded(self, trades: List[Dict], top_n: int = 5) -> List[Dict]:
        """Get most frequently traded symbols."""
        symbol_counts = {}
        symbol_pnl = {}
        
        for trade in trades:
            symbol = trade.get('symbol')
            if symbol:
                symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
                symbol_pnl[symbol] = symbol_pnl.get(symbol, 0.0) + trade.get('pnl', 0.0)
        
        # Sort by count
        sorted_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        
        result = []
        for symbol, count in sorted_symbols:
            result.append({
                'symbol': symbol,
                'trades': count,
                'total_pnl': round(symbol_pnl[symbol], 2)
            })
        
        return result
    
    def get_open_positions(self) -> List[Dict]:
        """
        Get all trades that have been entered but not exited.
        
        Returns:
            List of open position records
        """
        open_positions = [
            t for t in self.trades
            if t['type'] == 'entry' and t.get('exit_price') is None
        ]
        
        return open_positions
    
    def export_to_csv(self, output_file: str):
        """
        Export trade journal to CSV for external analysis.
        
        Args:
            output_file: Path to output CSV file
        """
        try:
            import csv
            
            # Flatten trade records for CSV
            rows = []
            for trade in self.trades:
                row = {
                    'type': trade.get('type'),
                    'symbol': trade.get('symbol'),
                    'date': trade.get('date'),
                    'time': trade.get('time'),
                    'entry_price': trade.get('price'),
                    'exit_price': trade.get('exit_price'),
                    'qty': trade.get('qty'),
                    'pnl': trade.get('pnl'),
                    'pnl_pct': trade.get('pnl_pct'),
                    'hold_days': trade.get('hold_days'),
                    'strategy': trade.get('strategy'),
                    'confidence': trade.get('confidence'),
                    'exit_reason': trade.get('exit_reason'),
                    'skip_reason': trade.get('reason') if trade.get('type') == 'skip' else None
                }
                rows.append(row)
            
            if rows:
                with open(output_file, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                
                logger.info(f"Trade journal exported to {output_file}")
            else:
                logger.warning("No trades to export")
                
        except Exception as e:
            logger.error(f"Failed to export to CSV: {e}", exc_info=True)


if __name__ == '__main__':
    # Test the trade journal
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("TRADE JOURNAL TEST SUITE")
    print("=" * 60)
    
    journal = TradeJournal('/tmp/trade_journal_test.json')
    
    # Test entry recording
    print("\n1. Recording Entry")
    journal.record_entry(
        symbol='AAPL',
        price=150.0,
        qty=5,
        signals={'rsi': 30, 'macd': 'bullish', 'volume_spike': True},
        risk_check={'allowed': True, 'risk_pct': 2.0},
        confidence=0.75,
        strategy='momentum'
    )
    
    # Test skip recording
    print("\n2. Recording Skip")
    journal.record_skip(
        symbol='GME',
        reason='pdt_block',
        signals={'short_squeeze_signal': True}
    )
    
    # Test exit recording (simulated)
    print("\n3. Recording Exit")
    journal.record_exit(
        symbol='AAPL',
        price=155.0,
        qty=5,
        reason='take_profit',
        pnl=25.0,
        hold_days=3
    )
    
    # Test daily summary
    print("\n4. Daily Summary")
    summary = journal.daily_summary()
    print(f"   Trades: {summary['total_trades']}")
    print(f"   Exits: {summary['exits']}")
    print(f"   Skips: {summary['skips']}")
    print(f"   P&L: ${summary['total_pnl']:.2f}")
    print(f"   Win Rate: {summary['win_rate']:.1f}%")
    
    # Test performance report
    print("\n5. Performance Report (30 days)")
    report = journal.get_performance_report(days=30)
    print(f"   Total Trades: {report.get('total_trades', 0)}")
    print(f"   Total P&L: ${report.get('total_pnl', 0):.2f}")
    print(f"   Win Rate: {report.get('win_rate', 0):.1f}%")
    print(f"   Sharpe Ratio: {report.get('sharpe_ratio', 0):.2f}")
    print(f"   Max Drawdown: ${report.get('max_drawdown', 0):.2f}")
    
    # Test open positions
    print("\n6. Open Positions")
    open_pos = journal.get_open_positions()
    print(f"   Open positions: {len(open_pos)}")
    
    print("\n" + "=" * 60)
    print("TRADE JOURNAL INITIALIZED AND TESTED")
    print("=" * 60)
