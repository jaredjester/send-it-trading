"""
Monte Carlo simulation for tail risk and empirical Kelly sizing.

Usage:
    from core.monte_carlo import MonteCarloSimulator
    
    mc = MonteCarloSimulator(historical_returns, n_sims=10000)
    result = mc.analyze(kelly=0.69, current_size=0.69)
    
    print(f"p99 drawdown: {result['drawdown_dist']['p99']:.1%}")
    print(f"Recommended size: {result['recommended_size']:.1%}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloResult:
    """Monte Carlo analysis results."""
    
    kelly: float
    edge_cv: float
    empirical_kelly: float
    risk_adjusted_size: float
    current_size: float
    drawdown_dist: dict[str, float]
    verdict: str
    recommended_size: float
    paths_simulated: int
    n_periods: int
    
    def to_dict(self) -> dict[str, Any]:
        return {
            'kelly': self.kelly,
            'edge_cv': self.edge_cv,
            'empirical_kelly': self.empirical_kelly,
            'risk_adjusted_size': self.risk_adjusted_size,
            'current_size': self.current_size,
            'drawdown_dist': self.drawdown_dist,
            'verdict': self.verdict,
            'recommended_size': self.recommended_size,
            'paths_simulated': self.paths_simulated,
            'n_periods': self.n_periods
        }


class MonteCarloSimulator:
    """
    Monte Carlo simulator for tail risk measurement and position sizing.
    
    Resamples historical returns to generate thousands of alternative paths,
    measures drawdown distribution, and adjusts Kelly sizing for uncertainty.
    """
    
    def __init__(self, returns: list[float] | np.ndarray, n_sims: int = 10000):
        """
        Initialize simulator.
        
        Args:
            returns: Historical daily returns (e.g., [0.02, -0.01, 0.03])
            n_sims: Number of Monte Carlo paths to simulate
        """
        self.returns = np.array(returns)
        self.n_sims = n_sims
        
        if len(self.returns) < 20:
            logger.warning(f"Only {len(self.returns)} returns available. Need 20+ for robust analysis.")
    
    def simulate_paths(self, n_periods: int = 180) -> np.ndarray:
        """
        Generate Monte Carlo paths by resampling returns with replacement.
        
        Args:
            n_periods: Number of periods to simulate (default 180 days)
            
        Returns:
            Array of shape (n_sims, n_periods) with cumulative returns
        """
        paths = np.zeros((self.n_sims, n_periods))
        
        for i in range(self.n_sims):
            # Randomly sample returns with replacement
            sampled = np.random.choice(self.returns, size=n_periods, replace=True)
            # Calculate cumulative returns
            paths[i] = np.cumprod(1 + sampled) - 1
        
        return paths
    
    def calculate_drawdowns(self, paths: np.ndarray) -> np.ndarray:
        """
        Calculate maximum drawdown for each path.
        
        Args:
            paths: Array of cumulative returns (n_sims, n_periods)
            
        Returns:
            Array of max drawdowns for each path (n_sims,)
        """
        max_drawdowns = []
        
        for path in paths:
            # Calculate running maximum (peak)
            peak = np.maximum.accumulate(1 + path)
            # Drawdown = (value - peak) / peak
            drawdown = ((1 + path) - peak) / peak
            # Max drawdown is the worst (most negative)
            max_drawdowns.append(drawdown.min())
        
        return np.array(max_drawdowns)
    
    def get_drawdown_distribution(self, paths: np.ndarray) -> dict[str, float]:
        """
        Get percentile distribution of max drawdowns.
        
        Args:
            paths: Array of cumulative returns
            
        Returns:
            Dict with p50, p90, p95, p99, and max drawdown
        """
        dd = self.calculate_drawdowns(paths)
        
        return {
            'p50': float(np.percentile(dd, 50)),  # Median
            'p90': float(np.percentile(dd, 90)),  # 1 in 10
            'p95': float(np.percentile(dd, 95)),  # 1 in 20
            'p99': float(np.percentile(dd, 99)),  # 1 in 100
            'max': float(dd.min())  # Worst case
        }
    
    def calculate_edge_cv(self) -> float:
        """
        Calculate coefficient of variation (CV) of edge.
        
        CV = std / mean
        Higher CV = more uncertainty = smaller position size
        
        Returns:
            Coefficient of variation
        """
        mean = self.returns.mean()
        std = self.returns.std()
        
        # Avoid division by zero
        if abs(mean) < 0.0001:
            return 0.9  # High uncertainty
        
        cv = abs(std / mean)
        # Cap at 0.9 to prevent extreme adjustments
        return min(cv, 0.9)
    
    def empirical_kelly(self, kelly: float) -> float:
        """
        Adjust Kelly fraction for edge uncertainty.
        
        Formula: f_empirical = f_kelly × (1 - CV_edge)
        
        Args:
            kelly: Theoretical Kelly fraction
            
        Returns:
            Empirical Kelly adjusted for uncertainty
        """
        cv = self.calculate_edge_cv()
        adjusted = kelly * (1 - cv)
        return max(0.0, adjusted)
    
    def analyze(
        self,
        kelly: float,
        current_size: float,
        max_drawdown_tolerance: float = 0.25,
        n_periods: int = 180
    ) -> MonteCarloResult:
        """
        Full Monte Carlo analysis with sizing recommendation.
        
        Args:
            kelly: Conviction-based Kelly fraction (e.g., 0.69)
            current_size: Current position size as fraction (e.g., 0.69)
            max_drawdown_tolerance: Max acceptable p95 drawdown (default 0.25 = 25%)
            n_periods: Simulation horizon in days (default 180)
            
        Returns:
            MonteCarloResult with sizing recommendation
        """
        # Simulate paths
        paths = self.simulate_paths(n_periods)
        
        # Get drawdown distribution
        dd_dist = self.get_drawdown_distribution(paths)
        
        # Calculate empirical Kelly (adjusted for uncertainty)
        emp_kelly = self.empirical_kelly(kelly)
        
        # Risk-adjust if p95 drawdown exceeds tolerance
        risk_adjusted = emp_kelly
        if abs(dd_dist['p95']) > max_drawdown_tolerance:
            # Scale down to keep p95 under tolerance
            risk_adjusted *= (max_drawdown_tolerance / abs(dd_dist['p95']))
        
        # Determine verdict
        verdict = self._size_verdict(current_size, risk_adjusted)
        
        return MonteCarloResult(
            kelly=kelly,
            edge_cv=self.calculate_edge_cv(),
            empirical_kelly=emp_kelly,
            risk_adjusted_size=risk_adjusted,
            current_size=current_size,
            drawdown_dist=dd_dist,
            verdict=verdict,
            recommended_size=risk_adjusted,
            paths_simulated=self.n_sims,
            n_periods=n_periods
        )
    
    def _size_verdict(self, current: float, recommended: float) -> str:
        """Determine if position is oversized, undersized, or optimal."""
        if recommended == 0:
            return 'NO_EDGE'
        
        diff_pct = (current - recommended) / recommended
        
        if diff_pct > 0.30:
            return 'OVERSIZED'
        elif diff_pct > 0.15:
            return 'SLIGHTLY_OVERSIZED'
        elif diff_pct < -0.30:
            return 'UNDERSIZED'
        elif diff_pct < -0.15:
            return 'SLIGHTLY_UNDERSIZED'
        else:
            return 'OPTIMAL'
    
    def print_report(self, result: MonteCarloResult, symbol: str = "") -> None:
        """
        Print human-readable Monte Carlo report.
        
        Args:
            result: Monte Carlo analysis result
            symbol: Optional symbol name for report header
        """
        header = f"Monte Carlo Analysis - {symbol}" if symbol else "Monte Carlo Analysis"
        print(f"\n{'=' * 60}")
        print(f"{header}")
        print(f"{'=' * 60}\n")
        
        print(f"Simulations: {result.paths_simulated:,} paths × {result.n_periods} days\n")
        
        print("Position Sizing:")
        print(f"  Kelly (conviction):     {result.kelly:>6.1%}")
        print(f"  Edge CV (uncertainty):  {result.edge_cv:>6.2f}")
        print(f"  Empirical Kelly:        {result.empirical_kelly:>6.1%}")
        print(f"  Risk Adjusted:          {result.risk_adjusted_size:>6.1%}")
        print(f"  Current Size:           {result.current_size:>6.1%}\n")
        
        print("Drawdown Distribution:")
        print(f"  Median (p50):           {result.drawdown_dist['p50']:>6.1%}")
        print(f"  1-in-10 (p90):          {result.drawdown_dist['p90']:>6.1%}")
        print(f"  1-in-20 (p95):          {result.drawdown_dist['p95']:>6.1%}")
        print(f"  1-in-100 (p99):         {result.drawdown_dist['p99']:>6.1%}")
        print(f"  Worst case (max):       {result.drawdown_dist['max']:>6.1%}\n")
        
        print(f"Verdict: {result.verdict}")
        print(f"Recommendation: {result.recommended_size:.1%}")
        
        if result.verdict in ('OVERSIZED', 'SLIGHTLY_OVERSIZED'):
            reduction = (result.current_size - result.recommended_size) * 100
            print(f"\n⚠️  REDUCE position by {reduction:.1f} percentage points")
            print(f"   OR accept 1-in-20 chance of {result.drawdown_dist['p95']:.1%} drawdown")
        elif result.verdict in ('UNDERSIZED', 'SLIGHTLY_UNDERSIZED'):
            increase = (result.recommended_size - result.current_size) * 100
            print(f"\n✅ Can INCREASE position by {increase:.1f} percentage points")
        else:
            print(f"\n✅ Position size is OPTIMAL")
        
        print(f"\n{'=' * 60}\n")


def quick_analysis(returns: list[float], kelly: float, current_size: float, symbol: str = "") -> dict:
    """
    Quick Monte Carlo analysis helper.
    
    Args:
        returns: Historical returns
        kelly: Kelly fraction
        current_size: Current position size
        symbol: Optional symbol name
        
    Returns:
        Analysis results as dict
    """
    mc = MonteCarloSimulator(returns, n_sims=10000)
    result = mc.analyze(kelly, current_size)
    mc.print_report(result, symbol)
    return result.to_dict()


if __name__ == "__main__":
    # Example usage
    import sys
    
    # Simulate some returns (replace with real data)
    np.random.seed(42)
    sample_returns = np.random.normal(0.001, 0.02, 100)  # Mean 0.1%, std 2%
    
    mc = MonteCarloSimulator(sample_returns, n_sims=10000)
    result = mc.analyze(kelly=0.69, current_size=0.69)
    mc.print_report(result, "GME")
