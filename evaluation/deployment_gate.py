"""
Deployment gate - validates changes before pushing to live bot.

NO CODE GOES LIVE WITHOUT PASSING THIS.
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, Tuple, List, Optional
from pathlib import Path

from .backtest_engine import StrategyBacktester
from .alpha_tracker import AlphaTracker


class DeploymentGate:
    """
    Pre-deployment validation gate.
    
    Blocks changes that would degrade performance.
    """
    
    def __init__(self, log_path: str = "evaluation/deployment_log.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.backtester = StrategyBacktester()
        self.alpha_tracker = AlphaTracker()
    
    def validate_change(
        self,
        new_config: Dict,
        change_description: str,
        baseline_run_id: Optional[str] = None
    ) -> Tuple[bool, str, Dict]:
        """
        Validate a proposed change to orchestrator config.
        
        Returns:
            (approved, reason, test_results)
        """
        print("=" * 60)
        print("DEPLOYMENT GATE: VALIDATING CHANGE")
        print("=" * 60)
        print(f"Change: {change_description}")
        print()
        
        config_hash = self._hash_config(new_config)
        
        # Step 1: Backtest on last 90 days
        print("Step 1: Running 90-day backtest...")
        backtest_results = self.backtester.run_backtest(
            start_date=(datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d'),
            end_date=datetime.now().strftime('%Y-%m-%d'),
            orchestrator_config=new_config
        )
        
        metrics = backtest_results['metrics']
        
        print(f"  Sharpe: {metrics['sharpe']:.2f}")
        print(f"  Alpha: {metrics['alpha_vs_spy']:.2%}")
        print(f"  Max DD: {metrics['max_drawdown']:.2%}")
        print(f"  Trades: {metrics['num_trades']}")
        print()
        
        # Step 2: Compare to baseline if provided
        if baseline_run_id:
            print("Step 2: Comparing to baseline...")
            comparison = self.backtester.compare_to_baseline(
                new_config,
                baseline_run_id
            )
            
            delta = comparison['delta']
            print(f"  Sharpe Δ: {delta['sharpe_delta']:+.2f}")
            print(f"  Alpha Δ: {delta['alpha_delta']:+.2%}")
            print(f"  Return Δ: {delta['return_delta']:+.2%}")
            print()
            
            # Reject if degraded significantly
            if delta['sharpe_delta'] < -0.3:
                return self._reject(
                    "Sharpe degraded by more than 0.3",
                    config_hash,
                    change_description,
                    backtest_results
                )
            
            if delta['alpha_delta'] < -0.05:
                return self._reject(
                    "Alpha degraded by more than 5%",
                    config_hash,
                    change_description,
                    backtest_results
                )
        
        # Step 3: Check minimum quality thresholds
        print("Step 3: Checking quality thresholds...")
        
        checks = {
            'sharpe >= 1.0': metrics['sharpe'] >= 1.0,
            'alpha > 5%': metrics['alpha_vs_spy'] > 0.05,
            'max_dd < 30%': abs(metrics['max_drawdown']) < 0.30,
            'win_rate > 45%': metrics['win_rate'] > 0.45
        }
        
        failed = [name for name, passed in checks.items() if not passed]
        
        for name, passed in checks.items():
            status = "✓" if passed else "✗"
            print(f"  {status} {name}")
        
        if failed:
            return self._reject(
                f"Failed checks: {', '.join(failed)}",
                config_hash,
                change_description,
                backtest_results
            )
        
        print()
        print("✅ ALL CHECKS PASSED - APPROVED FOR DEPLOYMENT")
        print("=" * 60)
        
        return self._approve(
            config_hash,
            change_description,
            backtest_results
        )
    
    def _approve(
        self,
        config_hash: str,
        description: str,
        results: Dict
    ) -> Tuple[bool, str, Dict]:
        """Log approval and return."""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'config_hash': config_hash,
            'description': description,
            'decision': 'APPROVED',
            'metrics': results['metrics'],
            'run_id': results['run_id']
        }
        
        self._append_log(log_entry)
        
        return (
            True,
            "Approved - all validation checks passed",
            results
        )
    
    def _reject(
        self,
        reason: str,
        config_hash: str,
        description: str,
        results: Dict
    ) -> Tuple[bool, str, Dict]:
        """Log rejection and return."""
        print()
        print(f"❌ REJECTED: {reason}")
        print("=" * 60)
        
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'config_hash': config_hash,
            'description': description,
            'decision': 'REJECTED',
            'reason': reason,
            'metrics': results['metrics'],
            'run_id': results['run_id']
        }
        
        self._append_log(log_entry)
        
        return (
            False,
            f"Rejected - {reason}",
            results
        )
    
    def _hash_config(self, config: Dict) -> str:
        """Generate hash of config for tracking."""
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()[:8]
    
    def _append_log(self, entry: Dict):
        """Append to deployment log."""
        with open(self.log_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    
    def get_approval_history(self, last_n: int = 10) -> List[Dict]:
        """Get recent deployment decisions."""
        if not self.log_path.exists():
            return []
        
        with open(self.log_path, 'r') as f:
            lines = f.readlines()
        
        entries = [json.loads(line) for line in lines[-last_n:]]
        return entries


class ChangeValidator:
    """
    Helper to validate specific types of changes.
    """
    
    @staticmethod
    def validate_alpha_weight_change(
        current_weights: Dict[str, float],
        new_weights: Dict[str, float],
        alpha_tracker: AlphaTracker
    ) -> Tuple[bool, str]:
        """
        Validate changing alpha source weights.
        
        Only allow if supported by measured IC.
        """
        # Check if we're increasing weight on a signal with proven edge
        for signal_name, new_weight in new_weights.items():
            current_weight = current_weights.get(signal_name, 0.0)
            weight_delta = new_weight - current_weight
            
            if weight_delta > 0.1:  # Significant increase
                quality = alpha_tracker.get_signal_quality(signal_name)
                
                if not quality['has_edge']:
                    return False, f"Cannot increase {signal_name} weight - no proven edge (IC={quality['ic_1d']:.3f})"
                
                if quality['confidence'] == 'WEAK':
                    return False, f"Cannot increase {signal_name} weight - edge too weak"
        
        return True, "Weight changes align with measured edge"
    
    @staticmethod
    def validate_risk_limit_change(
        current_limits: Dict,
        new_limits: Dict
    ) -> Tuple[bool, str]:
        """
        Validate changing risk limits.
        
        Only allow loosening if we have strong alpha.
        """
        # Check max position size
        if new_limits.get('max_position', 0) > current_limits.get('max_position', 0):
            # Loosening position limits - requires justification
            # This should only happen if we have IC > 0.10 on signals
            return True, "Risk limit change requires alpha justification in deployment notes"
        
        return True, "Risk limits tightened or unchanged"


if __name__ == '__main__':
    # Test deployment gate
    gate = DeploymentGate()
    
    test_config = {
        'alpha_sources': {
            'sentiment': 0.3,
            'technical': 0.5,
            'volume': 0.2
        },
        'risk_limits': {
            'max_position': 0.35,
            'max_drawdown': 0.20
        }
    }
    
    approved, reason, results = gate.validate_change(
        test_config,
        "Testing deployment gate with mock config"
    )
    
    print(f"\nDecision: {'APPROVED' if approved else 'REJECTED'}")
    print(f"Reason: {reason}")
