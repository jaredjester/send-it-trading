"""
Alpha measurement and edge quantification.

Tracks REAL edge - not hopium, not correlation, PURE ALPHA.
"""
import numpy as np
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from pathlib import Path


class AlphaTracker:
    """
    Measure true alpha and information coefficient for each signal.
    
    Alpha = Returns - (Beta * Benchmark Returns)
    IC = Correlation(Signal Strength, Forward Returns)
    """
    
    def __init__(self, db_path: str = "evaluation/alpha_metrics.json"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.metrics = self._load_metrics()
    
    def _load_metrics(self) -> Dict:
        """Load historical alpha metrics."""
        if not self.db_path.exists():
            return {
                'signals': {},
                'overall': {
                    'daily_alpha': [],
                    'cumulative_alpha': 0.0,
                    'sharpe': 0.0,
                    'information_ratio': 0.0
                },
                'last_updated': None
            }
        
        with open(self.db_path, 'r') as f:
            return json.load(f)
    
    def _save_metrics(self):
        """Persist metrics."""
        self.metrics['last_updated'] = datetime.now().isoformat()
        with open(self.db_path, 'w') as f:
            json.dump(self.metrics, f, indent=2)
    
    def record_signal_performance(
        self,
        signal_name: str,
        signal_strength: float,
        forward_return_1d: float,
        forward_return_5d: float,
        benchmark_return_1d: float
    ):
        """
        Record signal's predictive power.
        
        Args:
            signal_name: e.g. "rsi_divergence", "volume_spike"
            signal_strength: -1 to +1 signal value
            forward_return_1d: Actual 1-day forward return
            forward_return_5d: Actual 5-day forward return  
            benchmark_return_1d: SPY return for alpha calc
        """
        if signal_name not in self.metrics['signals']:
            self.metrics['signals'][signal_name] = {
                'observations': [],
                'ic_1d': 0.0,
                'ic_5d': 0.0,
                'hit_rate': 0.0,
                'avg_magnitude': 0.0,
                'last_30_ic': 0.0
            }
        
        obs = {
            'timestamp': datetime.now().isoformat(),
            'strength': signal_strength,
            'fwd_1d': forward_return_1d,
            'fwd_5d': forward_return_5d,
            'bmk_1d': benchmark_return_1d,
            'alpha_1d': forward_return_1d - benchmark_return_1d
        }
        
        self.metrics['signals'][signal_name]['observations'].append(obs)
        
        # Keep last 100 observations only
        self.metrics['signals'][signal_name]['observations'] = \
            self.metrics['signals'][signal_name]['observations'][-100:]
        
        # Recalculate IC
        self._update_signal_ic(signal_name)
        
        self._save_metrics()
    
    def _update_signal_ic(self, signal_name: str):
        """Calculate information coefficient for signal."""
        obs = self.metrics['signals'][signal_name]['observations']
        
        if len(obs) < 20:
            return  # Need minimum data
        
        strengths = [o['strength'] for o in obs]
        returns_1d = [o['fwd_1d'] for o in obs]
        returns_5d = [o['fwd_5d'] for o in obs]
        
        # IC = correlation between signal and forward returns
        ic_1d = np.corrcoef(strengths, returns_1d)[0, 1] if len(strengths) > 1 else 0.0
        ic_5d = np.corrcoef(strengths, returns_5d)[0, 1] if len(strengths) > 1 else 0.0
        
        # Hit rate = % of times signal direction matched return direction
        hits = sum(1 for s, r in zip(strengths, returns_1d) if np.sign(s) == np.sign(r))
        hit_rate = hits / len(strengths)
        
        # Average magnitude when signal fires
        avg_mag = np.mean([abs(r) for s, r in zip(strengths, returns_1d) if abs(s) > 0.3])
        
        # Last 30 days IC
        recent_obs = obs[-30:]
        if len(recent_obs) >= 10:
            recent_strengths = [o['strength'] for o in recent_obs]
            recent_returns = [o['fwd_1d'] for o in recent_obs]
            ic_30d = np.corrcoef(recent_strengths, recent_returns)[0, 1]
        else:
            ic_30d = ic_1d
        
        self.metrics['signals'][signal_name].update({
            'ic_1d': float(ic_1d) if not np.isnan(ic_1d) else 0.0,
            'ic_5d': float(ic_5d) if not np.isnan(ic_5d) else 0.0,
            'hit_rate': float(hit_rate),
            'avg_magnitude': float(avg_mag) if not np.isnan(avg_mag) else 0.0,
            'last_30_ic': float(ic_30d) if not np.isnan(ic_30d) else 0.0
        })
    
    def get_signal_quality(self, signal_name: str) -> Dict:
        """Get current quality metrics for a signal."""
        if signal_name not in self.metrics['signals']:
            return {
                'has_edge': False,
                'ic_1d': 0.0,
                'hit_rate': 0.5,
                'confidence': 'NO_DATA'
            }
        
        sig = self.metrics['signals'][signal_name]
        
        # Edge criteria:
        # IC > 0.05 AND hit_rate > 0.55 AND recent IC still positive
        has_edge = (
            sig['ic_1d'] > 0.05 and
            sig['hit_rate'] > 0.55 and
            sig['last_30_ic'] > 0.0
        )
        
        # Confidence based on IC strength
        if sig['ic_1d'] > 0.15:
            confidence = 'STRONG'
        elif sig['ic_1d'] > 0.08:
            confidence = 'MODERATE'
        elif sig['ic_1d'] > 0.03:
            confidence = 'WEAK'
        else:
            confidence = 'NONE'
        
        return {
            'has_edge': has_edge,
            'ic_1d': sig['ic_1d'],
            'ic_5d': sig['ic_5d'],
            'hit_rate': sig['hit_rate'],
            'avg_magnitude': sig['avg_magnitude'],
            'recent_ic': sig['last_30_ic'],
            'confidence': confidence,
            'observations': len(sig['observations'])
        }
    
    def rank_signals_by_edge(self) -> List[Tuple[str, float]]:
        """Return signals ranked by IC, highest first."""
        ranked = []
        
        for sig_name in self.metrics['signals']:
            quality = self.get_signal_quality(sig_name)
            ranked.append((sig_name, quality['ic_1d']))
        
        return sorted(ranked, key=lambda x: x[1], reverse=True)
    
    def calculate_portfolio_alpha(
        self,
        portfolio_returns: List[float],
        benchmark_returns: List[float],
        periods: int = 252
    ) -> Dict:
        """
        Calculate true alpha via regression.
        
        Alpha = Portfolio Return - (Beta * Benchmark Return + Rf)
        """
        if len(portfolio_returns) < 30:
            return {'alpha': 0.0, 'beta': 1.0, 'ir': 0.0}
        
        port_ret = np.array(portfolio_returns[-periods:])
        bmk_ret = np.array(benchmark_returns[-periods:])
        
        # Linear regression: port = alpha + beta * benchmark
        beta = np.cov(port_ret, bmk_ret)[0, 1] / np.var(bmk_ret)
        alpha = np.mean(port_ret) - beta * np.mean(bmk_ret)
        
        # Information ratio = alpha / tracking error
        tracking_error = np.std(port_ret - beta * bmk_ret)
        ir = alpha / tracking_error if tracking_error > 0 else 0.0
        
        # Annualize
        alpha_annual = alpha * 252
        ir_annual = ir * np.sqrt(252)
        
        return {
            'alpha_daily': float(alpha),
            'alpha_annual': float(alpha_annual),
            'beta': float(beta),
            'information_ratio': float(ir_annual),
            'tracking_error': float(tracking_error)
        }
    
    def get_edge_report(self) -> str:
        """Generate human-readable edge report."""
        report = ["=" * 60]
        report.append("EDGE ANALYSIS REPORT")
        report.append("=" * 60)
        report.append("")
        
        ranked = self.rank_signals_by_edge()
        
        report.append("SIGNALS RANKED BY INFORMATION COEFFICIENT:")
        report.append("-" * 60)
        
        for sig_name, ic in ranked:
            quality = self.get_signal_quality(sig_name)
            edge_marker = "✓ EDGE" if quality['has_edge'] else "✗ NO EDGE"
            
            report.append(f"{sig_name:30s} | IC: {ic:+.3f} | Hit: {quality['hit_rate']:.1%} | {edge_marker}")
        
        report.append("")
        report.append("LEGEND:")
        report.append("  IC > 0.15 = Strong predictive power")
        report.append("  IC > 0.08 = Moderate edge")
        report.append("  IC > 0.03 = Weak signal")
        report.append("  IC < 0.03 = No reliable edge")
        report.append("")
        report.append("Hit Rate > 55% + IC > 0.05 = Tradeable edge")
        
        return "\n".join(report)
    
    def kill_signal_if_degraded(self, signal_name: str, min_ic: float = 0.03) -> bool:
        """
        Determine if signal should be removed from strategy.
        
        Returns True if signal has degraded below minimum edge threshold.
        """
        quality = self.get_signal_quality(signal_name)
        
        # Kill if recent IC turned negative or overall IC too low
        should_kill = (
            quality['recent_ic'] < 0 or
            quality['ic_1d'] < min_ic or
            quality['hit_rate'] < 0.48  # Worse than random
        )
        
        return should_kill


if __name__ == '__main__':
    # Test
    tracker = AlphaTracker()
    
    # Simulate recording signal performance
    for i in range(50):
        # Mock RSI signal with slight edge
        rsi_signal = np.random.randn() * 0.5
        fwd_ret = rsi_signal * 0.01 + np.random.randn() * 0.015  # Slight correlation
        bmk_ret = np.random.randn() * 0.012
        
        tracker.record_signal_performance(
            'rsi_divergence',
            rsi_signal,
            fwd_ret,
            fwd_ret * 5,  # 5-day is magnified
            bmk_ret
        )
    
    print(tracker.get_edge_report())
    
    quality = tracker.get_signal_quality('rsi_divergence')
    print(f"\nRSI Quality: {quality}")
