"""
Rapid iteration workflow for Strategy V2.

The surgical, high-velocity improvement loop.
"""
import sys
import json
from datetime import datetime, timedelta
from typing import Dict, Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_project_root
from evaluation.deployment_gate import DeploymentGate, ChangeValidator
from evaluation.alpha_tracker import AlphaTracker
from evaluation.backtest_engine import StrategyBacktester


class RapidIterationWorkflow:
    """
    High-velocity improvement cycle.
    
    1. Propose change
    2. Validate with backtest
    3. Check alpha justification
    4. Deploy if approved
    5. Monitor for 24h
    6. Lock in if good, revert if bad
    """
    
    def __init__(self):
        self.gate = DeploymentGate()
        self.alpha_tracker = AlphaTracker()
        self.backtester = StrategyBacktester()
        
        self.config_path = get_project_root() / "master_config.json"
        self.current_config = self._load_current_config()
    
    def _load_current_config(self) -> Dict:
        """Load current live config."""
        if not self.config_path.exists():
            return {}
        
        with open(self.config_path, 'r') as f:
            return json.load(f)
    
    def _save_config(self, config: Dict, backup: bool = True):
        """Save config to live location."""
        if backup:
            backup_path = self.config_path.with_suffix(
                f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    current = json.load(f)
                with open(backup_path, 'w') as f:
                    json.dump(current, f, indent=2)
        
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    def propose_change(
        self,
        change_type: str,
        change_params: Dict,
        description: str,
        baseline_run_id: Optional[str] = None
    ) -> bool:
        """
        Propose and validate a change.
        
        Args:
            change_type: 'alpha_weights', 'risk_limits', 'conviction_params'
            change_params: New values
            description: Human explanation of why
            baseline_run_id: Optional comparison baseline
        
        Returns:
            True if deployed, False if rejected
        """
        print("\n" + "=" * 70)
        print(f"PROPOSING CHANGE: {change_type}")
        print("=" * 70)
        print(f"Description: {description}")
        print()
        
        # Build new config
        new_config = self.current_config.copy()
        new_config.update(change_params)
        
        # Validate change logic
        if change_type == 'alpha_weights':
            valid, reason = ChangeValidator.validate_alpha_weight_change(
                self.current_config.get('alpha_sources', {}),
                change_params.get('alpha_sources', {}),
                self.alpha_tracker
            )
            
            if not valid:
                print(f"❌ Change validation failed: {reason}")
                return False
        
        elif change_type == 'risk_limits':
            valid, reason = ChangeValidator.validate_risk_limit_change(
                self.current_config.get('risk_limits', {}),
                change_params.get('risk_limits', {})
            )
            
            if not valid:
                print(f"❌ Risk limit validation failed: {reason}")
                return False
        
        # Run through deployment gate
        approved, reason, results = self.gate.validate_change(
            new_config,
            description,
            baseline_run_id
        )
        
        if not approved:
            print(f"\n❌ DEPLOYMENT BLOCKED: {reason}")
            return False
        
        # Deploy
        print("\n🚀 DEPLOYING CHANGE...")
        self._save_config(new_config, backup=True)
        
        print("✅ DEPLOYED")
        print(f"Backup saved: {self.config_path.with_suffix('.backup_*')}")
        print("\n⚠️ MONITOR FOR 24H:")
        print("  - Check decision logs for unexpected behavior")
        print("  - Verify alpha metrics don't degrade")
        print("  - Watch for execution errors")
        print()
        
        return True
    
    def quick_alpha_boost_experiment(self, signal_name: str, weight_increase: float = 0.1):
        """
        Quick experiment: increase weight on high-IC signal.
        
        If signal has proven edge (IC > 0.10), boost its weight.
        """
        quality = self.alpha_tracker.get_signal_quality(signal_name)
        
        if not quality['has_edge']:
            print(f"❌ {signal_name} has no proven edge (IC={quality['ic_1d']:.3f})")
            return False
        
        if quality['confidence'] not in ['STRONG', 'MODERATE']:
            print(f"❌ {signal_name} edge too weak (confidence={quality['confidence']})")
            return False
        
        current_weight = self.current_config.get('alpha_sources', {}).get(signal_name, 0.0)
        new_weight = min(current_weight + weight_increase, 1.0)
        
        return self.propose_change(
            change_type='alpha_weights',
            change_params={
                'alpha_sources': {
                    **self.current_config.get('alpha_sources', {}),
                    signal_name: new_weight
                }
            },
            description=f"Boost {signal_name} weight from {current_weight:.2f} to {new_weight:.2f} (IC={quality['ic_1d']:.3f})"
        )
    
    def kill_dead_signal(self, signal_name: str):
        """
        Remove signal that has lost edge.
        """
        quality = self.alpha_tracker.get_signal_quality(signal_name)
        
        should_kill = self.alpha_tracker.kill_signal_if_degraded(signal_name)
        
        if not should_kill:
            print(f"ℹ️ {signal_name} still has edge - not killing")
            print(f"  IC: {quality['ic_1d']:.3f}, Hit Rate: {quality['hit_rate']:.1%}")
            return False
        
        # Remove from config
        alpha_sources = self.current_config.get('alpha_sources', {}).copy()
        if signal_name in alpha_sources:
            del alpha_sources[signal_name]
        
        return self.propose_change(
            change_type='alpha_weights',
            change_params={'alpha_sources': alpha_sources},
            description=f"Kill {signal_name} - degraded edge (IC={quality['ic_1d']:.3f}, recent_IC={quality['recent_ic']:.3f})"
        )
    
    def increase_position_size_if_alpha_strong(self):
        """
        Increase max position size if we have strong proven alpha.
        
        Only loosens risk if IC > 0.12 on multiple signals.
        """
        # Check if we have strong alpha
        ranked_signals = self.alpha_tracker.rank_signals_by_edge()
        
        strong_signals = [
            (name, ic) for name, ic in ranked_signals
            if ic > 0.12
        ]
        
        if len(strong_signals) < 2:
            print("❌ Not enough strong alpha signals to justify looser risk limits")
            print(f"  Need 2+ signals with IC > 0.12, have {len(strong_signals)}")
            return False
        
        current_max = self.current_config.get('risk_limits', {}).get('max_position', 0.30)
        new_max = min(current_max + 0.05, 0.50)  # Cap at 50%
        
        print(f"\n✅ Strong alpha detected:")
        for name, ic in strong_signals[:3]:
            print(f"  {name}: IC = {ic:.3f}")
        
        return self.propose_change(
            change_type='risk_limits',
            change_params={
                'risk_limits': {
                    **self.current_config.get('risk_limits', {}),
                    'max_position': new_max
                }
            },
            description=f"Increase max position to {new_max:.0%} - justified by {len(strong_signals)} strong signals"
        )
    
    def revert_to_backup(self, backup_timestamp: str):
        """
        Revert to a previous config backup.
        
        Use if deployed change degraded performance.
        """
        backup_pattern = f"master_config.backup_{backup_timestamp}*.json"
        backups = list(self.config_path.parent.glob(backup_pattern))
        
        if not backups:
            print(f"❌ No backup found matching {backup_pattern}")
            return False
        
        backup_file = backups[0]
        
        with open(backup_file, 'r') as f:
            backup_config = json.load(f)
        
        print(f"\n⚠️ REVERTING TO BACKUP: {backup_file.name}")
        
        self._save_config(backup_config, backup=True)
        
        print("✅ REVERTED")
        print("Restart orchestrator to apply reverted config")
        
        return True


if __name__ == '__main__':
    # Test workflow
    workflow = RapidIterationWorkflow()
    
    print("Available commands:")
    print("  1. Boost high-IC signal weight")
    print("  2. Kill degraded signal")
    print("  3. Increase position size (if alpha strong)")
    print("  4. Revert to backup")
    
    # Mock: boost a signal
    # workflow.quick_alpha_boost_experiment('rsi_divergence', weight_increase=0.15)
