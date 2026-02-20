"""
Historical backtesting engine for Strategy V2.

Tests orchestrator changes on past data before deploying to live bot.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from pathlib import Path


class StrategyBacktester:
    """Replay historical market data through orchestrator."""
    
    def __init__(
        self,
        historical_data_path: str = "data/historical_trades.db",
        results_db: str = "evaluation/backtest_results.db"
    ):
        self.data_path = Path(historical_data_path)
        self.results_db = Path(results_db)
        self.results_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_results_db()
    
    def _init_results_db(self):
        """Create results database."""
        conn = sqlite3.connect(str(self.results_db))
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                config_hash TEXT,
                config_json TEXT,
                start_date TEXT,
                end_date TEXT,
                initial_capital REAL,
                final_capital REAL,
                total_return REAL,
                sharpe REAL,
                sortino REAL,
                max_drawdown REAL,
                win_rate REAL,
                num_trades INTEGER,
                avg_trade_return REAL,
                alpha_vs_spy REAL,
                beta_vs_spy REAL,
                information_ratio REAL,
                executed_at TEXT,
                notes TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_log (
                run_id TEXT,
                timestamp TEXT,
                symbol TEXT,
                action TEXT,
                price REAL,
                quantity REAL,
                reason TEXT,
                alpha_score REAL,
                alt_data_boost REAL,
                rl_action TEXT,
                conviction_score REAL,
                FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_metrics (
                run_id TEXT,
                date TEXT,
                portfolio_value REAL,
                cash REAL,
                positions_count INTEGER,
                daily_return REAL,
                sharpe_30d REAL,
                drawdown REAL,
                spy_return REAL,
                alpha_daily REAL,
                FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def run_backtest(
        self,
        start_date: str,
        end_date: str,
        orchestrator_config: Dict,
        initial_capital: float = 1000.0,
        benchmark: str = "SPY"
    ) -> Dict:
        """
        Run backtest with given config.
        
        Returns:
            {
                'run_id': str,
                'metrics': {...},
                'trades': [...],
                'daily_performance': [...]
            }
        """
        run_id = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        print(f"Starting backtest: {run_id}")
        print(f"Period: {start_date} to {end_date}")
        print(f"Initial capital: ${initial_capital:,.2f}")
        
        # TODO: Implement actual replay logic
        # For now, return mock structure
        
        metrics = {
            'total_return': 0.0,
            'sharpe': 0.0,
            'sortino': 0.0,
            'max_drawdown': 0.0,
            'win_rate': 0.0,
            'num_trades': 0,
            'alpha_vs_spy': 0.0,
            'beta_vs_spy': 0.0,
            'information_ratio': 0.0
        }
        
        return {
            'run_id': run_id,
            'metrics': metrics,
            'trades': [],
            'daily_performance': []
        }
    
    def compare_to_baseline(
        self,
        new_config: Dict,
        baseline_run_id: str,
        test_period_days: int = 90
    ) -> Dict:
        """
        Compare new config to baseline performance.
        
        Returns improvement/degradation metrics.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=test_period_days)
        
        # Run new config
        new_results = self.run_backtest(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            new_config
        )
        
        # Load baseline
        baseline_metrics = self._load_run_metrics(baseline_run_id)
        
        # Compare
        delta = {
            'sharpe_delta': new_results['metrics']['sharpe'] - baseline_metrics['sharpe'],
            'alpha_delta': new_results['metrics']['alpha_vs_spy'] - baseline_metrics['alpha_vs_spy'],
            'drawdown_delta': new_results['metrics']['max_drawdown'] - baseline_metrics['max_drawdown'],
            'return_delta': new_results['metrics']['total_return'] - baseline_metrics['total_return']
        }
        
        # Determine if change is improvement
        is_improvement = (
            delta['sharpe_delta'] > 0 and
            delta['alpha_delta'] > 0 and
            delta['drawdown_delta'] > -0.05  # Allow 5% worse DD if returns justify
        )
        
        return {
            'new_run': new_results,
            'baseline_run_id': baseline_run_id,
            'delta': delta,
            'is_improvement': is_improvement,
            'recommendation': 'DEPLOY' if is_improvement else 'REJECT'
        }
    
    def _load_run_metrics(self, run_id: str) -> Dict:
        """Load metrics from previous run."""
        conn = sqlite3.connect(str(self.results_db))
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT sharpe, alpha_vs_spy, max_drawdown, total_return
            FROM backtest_runs
            WHERE run_id = ?
        ''', (run_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {
                'sharpe': 0.0,
                'alpha_vs_spy': 0.0,
                'max_drawdown': 0.0,
                'total_return': 0.0
            }
        
        return {
            'sharpe': row[0],
            'alpha_vs_spy': row[1],
            'max_drawdown': row[2],
            'total_return': row[3]
        }
    
    def validate_deployment(
        self,
        config: Dict,
        min_sharpe: float = 1.5,
        min_alpha: float = 0.10,
        max_drawdown: float = 0.25
    ) -> Tuple[bool, str]:
        """
        Pre-deployment validation.
        
        Returns: (is_safe_to_deploy, reason)
        """
        # Run 90-day backtest
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        
        results = self.run_backtest(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            config
        )
        
        metrics = results['metrics']
        
        # Check thresholds
        checks = {
            'sharpe': (metrics['sharpe'] >= min_sharpe, f"Sharpe {metrics['sharpe']:.2f} < {min_sharpe}"),
            'alpha': (metrics['alpha_vs_spy'] >= min_alpha, f"Alpha {metrics['alpha_vs_spy']:.2%} < {min_alpha:.2%}"),
            'drawdown': (abs(metrics['max_drawdown']) <= max_drawdown, f"DD {metrics['max_drawdown']:.2%} > {max_drawdown:.2%}")
        }
        
        failed_checks = [reason for passed, reason in checks.values() if not passed]
        
        if failed_checks:
            return False, "; ".join(failed_checks)
        
        return True, "All checks passed"


if __name__ == '__main__':
    # Test
    bt = StrategyBacktester()
    
    config = {
        'alpha_weights': {'sentiment': 0.3, 'technical': 0.7},
        'risk_limits': {'max_position': 0.4}
    }
    
    results = bt.run_backtest('2024-01-01', '2024-12-31', config)
    print(f"Backtest complete: {results['run_id']}")
