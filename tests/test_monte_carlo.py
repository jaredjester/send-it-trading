"""Tests for Monte Carlo simulator."""
import numpy as np
import pytest

from core.monte_carlo import MonteCarloSimulator, quick_analysis


def test_monte_carlo_basic():
    """Basic Monte Carlo simulation."""
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 100)
    
    mc = MonteCarloSimulator(returns, n_sims=1000)
    paths = mc.simulate_paths(n_periods=30)
    
    assert paths.shape == (1000, 30)
    assert not np.isnan(paths).any()


def test_drawdown_calculation():
    """Test drawdown calculation."""
    returns = [0.05, -0.03, 0.02, -0.08, 0.04]
    mc = MonteCarloSimulator(returns, n_sims=100)
    
    paths = mc.simulate_paths(n_periods=20)
    dd = mc.calculate_drawdowns(paths)
    
    assert len(dd) == 100
    assert all(dd <= 0)  # Drawdowns are negative


def test_drawdown_distribution():
    """Test drawdown percentiles."""
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 50)
    mc = MonteCarloSimulator(returns, n_sims=1000)
    
    paths = mc.simulate_paths(n_periods=30)
    dist = mc.get_drawdown_distribution(paths)
    
    assert 'p50' in dist
    assert 'p95' in dist
    assert 'p99' in dist
    assert dist['p95'] <= dist['p50'] <= 0  # p95 worse than median


def test_edge_cv():
    """Test coefficient of variation calculation."""
    # Low volatility returns
    low_vol = [0.01] * 20
    mc_low = MonteCarloSimulator(low_vol)
    cv_low = mc_low.calculate_edge_cv()
    
    # High volatility returns
    high_vol = [0.05, -0.04, 0.06, -0.03] * 10
    mc_high = MonteCarloSimulator(high_vol)
    cv_high = mc_high.calculate_edge_cv()
    
    assert cv_high > cv_low  # Higher volatility = higher CV


def test_empirical_kelly():
    """Test empirical Kelly adjustment."""
    returns = [0.02, -0.01, 0.03, -0.02, 0.025]
    mc = MonteCarloSimulator(returns)
    
    kelly = 0.5
    emp = mc.empirical_kelly(kelly)
    
    # Should be less than Kelly due to uncertainty
    assert emp < kelly
    assert emp > 0


def test_full_analysis():
    """Test full Monte Carlo analysis."""
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 100)
    
    mc = MonteCarloSimulator(returns, n_sims=1000)
    result = mc.analyze(kelly=0.69, current_size=0.69)
    
    assert result.kelly == 0.69
    assert result.current_size == 0.69
    assert result.empirical_kelly < result.kelly
    assert result.verdict in ['OVERSIZED', 'OPTIMAL', 'UNDERSIZED', 'SLIGHTLY_OVERSIZED', 'SLIGHTLY_UNDERSIZED', 'NO_EDGE']
    assert 0 <= result.recommended_size <= 1


def test_oversized_detection():
    """Test oversized position detection."""
    # Very volatile returns
    returns = [0.10, -0.08, 0.12, -0.09, 0.11, -0.10] * 10
    mc = MonteCarloSimulator(returns, n_sims=1000)
    
    # Large position on volatile asset
    result = mc.analyze(kelly=0.80, current_size=0.80, max_drawdown_tolerance=0.20)
    
    assert result.verdict in ['OVERSIZED', 'SLIGHTLY_OVERSIZED']
    assert result.recommended_size < result.current_size


def test_quick_analysis():
    """Test quick analysis helper."""
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 50)
    
    result = quick_analysis(returns, kelly=0.50, current_size=0.50, symbol="TEST")
    
    assert 'kelly' in result
    assert 'drawdown_dist' in result
    assert 'verdict' in result


def test_zero_edge():
    """Test handling of zero edge."""
    # Zero returns
    returns = [0.0] * 50
    mc = MonteCarloSimulator(returns)
    
    result = mc.analyze(kelly=0.50, current_size=0.50)
    
    # Should recommend very small or zero size
    assert result.recommended_size < 0.10
