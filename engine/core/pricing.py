"""Monte Carlo + Black-Scholes pricing engine with Greeks."""
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
from dataclasses import dataclass
from typing import Literal, Tuple

OptionKind = Literal['call', 'put']


@dataclass
class OptionSpec:
    kind: OptionKind
    strike: float
    expiry: float      # years to expiry
    quantity: int = 1  # positive = long, negative = short


# ── Black-Scholes ──────────────────────────────────────────────────────────────

def bs_price(kind: OptionKind, S: float, K: float, T: float,
             r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, (S - K) if kind == 'call' else (K - S))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if kind == 'call':
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(kind: OptionKind, S: float, K: float, T: float,
              r: float, sigma: float) -> dict:
    if T <= 0:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'theta': 0.0, 'rho': 0.0}
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    nd1 = norm.pdf(d1)
    gamma = nd1 / (S * sigma * np.sqrt(T))
    vega  = S * nd1 * np.sqrt(T) / 100.0   # per 1% IV move
    if kind == 'call':
        delta = norm.cdf(d1)
        theta = (-(S * nd1 * sigma) / (2 * np.sqrt(T))
                 - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365.0
        rho   = K * T * np.exp(-r * T) * norm.cdf(d2) / 100.0
    else:
        delta = norm.cdf(d1) - 1
        theta = (-(S * nd1 * sigma) / (2 * np.sqrt(T))
                 + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365.0
        rho   = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100.0
    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'theta': theta, 'rho': rho}


def implied_vol(kind: OptionKind, S: float, K: float, T: float,
                r: float, market_price: float) -> float:
    """Solve for IV using Brent's method."""
    try:
        return brentq(
            lambda sigma: bs_price(kind, S, K, T, r, sigma) - market_price,
            1e-4, 10.0, xtol=1e-6
        )
    except ValueError:
        return 0.3  # fallback


# ── Monte Carlo (GBM) ──────────────────────────────────────────────────────────

def mc_price_gbm(kind: OptionKind, S: float, K: float, T: float,
                 r: float, sigma: float, n_paths: int = 10_000,
                 n_steps: int = 50) -> Tuple[float, np.ndarray]:
    """Returns (price, terminal_spot_paths)."""
    dt = T / n_steps
    paths = np.full(n_paths, S, dtype=np.float64)
    for _ in range(n_steps):
        z = np.random.standard_normal(n_paths)
        paths *= np.exp((r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z)
    payoffs = np.maximum(paths - K, 0) if kind == 'call' else np.maximum(K - paths, 0)
    price   = np.exp(-r * T) * np.mean(payoffs)
    return price, paths


# ── Monte Carlo (Heston stochastic vol) ───────────────────────────────────────

def mc_price_heston(kind: OptionKind, S: float, K: float, T: float,
                    r: float, v0: float,
                    kappa: float = 2.0, theta: float = 0.04,
                    eta: float = 0.4, rho: float = -0.7,
                    n_paths: int = 8_000, n_steps: int = 50) -> float:
    """Heston model MC price."""
    dt = T / n_steps
    S_paths = np.full(n_paths, S)
    v_paths = np.full(n_paths, v0)
    for _ in range(n_steps):
        z1 = np.random.standard_normal(n_paths)
        z2 = rho * z1 + np.sqrt(1 - rho**2) * np.random.standard_normal(n_paths)
        v_paths = np.maximum(
            v_paths + kappa * (theta - v_paths) * dt + eta * np.sqrt(np.maximum(v_paths, 0) * dt) * z2,
            0
        )
        S_paths *= np.exp((r - 0.5 * v_paths) * dt + np.sqrt(np.maximum(v_paths, 0) * dt) * z1)
    payoffs = np.maximum(S_paths - K, 0) if kind == 'call' else np.maximum(K - S_paths, 0)
    return np.exp(-r * T) * np.mean(payoffs)


# ── Greeks-only functions (for gamma scanner) ─────────────────────────────────

def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Compute Black-Scholes gamma for a single contract."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    try:
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        return norm.pdf(d1) / (S * sigma * np.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0


def bs_delta(kind: OptionKind, S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Compute Black-Scholes delta."""
    if T <= 0 or sigma <= 0:
        return (1.0 if S > K else 0.0) if kind == 'call' else (-1.0 if S < K else 0.0)
    try:
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        return norm.cdf(d1) if kind == 'call' else norm.cdf(d1) - 1.0
    except (ValueError, ZeroDivisionError):
        return 0.0


# ── Utility functions from train_synthetic.py ─────────────────────────────────

def gbm_path(S0, mu, sigma, T_years, steps):
    """Returns list of prices of length steps+1."""
    import math
    import random
    dt = T_years / steps
    prices = [S0]
    for _ in range(steps):
        z = random.gauss(0, 1)
        prices.append(prices[-1] * math.exp((mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z))
    return prices


def iv_path(iv0, steps, kappa=2.0, theta=None, xi=0.3):
    """Heston-style mean-reverting IV."""
    import math
    import random
    theta = theta or iv0
    dt = 1.0 / 252 / steps
    ivs = [iv0]
    for _ in range(steps):
        dv = kappa * (theta - ivs[-1]) * dt + xi * ivs[-1] * math.sqrt(dt) * random.gauss(0, 1)
        ivs.append(max(0.05, ivs[-1] + dv))
    return ivs


def pnl_distribution(spec: OptionSpec, entry_price: float, S: float, K: float,
                    T: float, r: float, sigma: float, n_scenarios: int = 1000) -> dict:
    """
    Generate P&L distribution for an option position at expiry.

    Args:
        spec: Option specification (kind, strike, expiry, quantity)
        entry_price: Premium paid/received per contract
        S: Current spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility
        n_scenarios: Number of Monte Carlo scenarios

    Returns:
        dict with keys: 'scenarios' (array of P&L values), 'ev' (expected value),
        'win_rate' (probability of profit), 'sharpe' (risk-adjusted return)
    """
    if T <= 0:
        # At expiry
        if spec.kind == 'call':
            payoff = max(0, S - K)
        else:  # put
            payoff = max(0, K - S)
        pnl = (payoff - entry_price) * spec.quantity * 100  # 100 shares per contract
        return {
            'scenarios': np.array([pnl]),
            'ev': pnl,
            'win_rate': 100.0 if pnl > 0 else 0.0,
            'sharpe': 0.0
        }

    # Monte Carlo simulation of spot prices at expiry
    dt = T
    scenarios = []

    for _ in range(n_scenarios):
        # Generate terminal spot price using GBM
        z = np.random.standard_normal()
        St = S * np.exp((r - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z)

        # Calculate payoff at expiry
        if spec.kind == 'call':
            payoff = max(0, St - K)
        else:  # put
            payoff = max(0, K - St)

        # Calculate P&L (payoff minus premium paid, times quantity and contract multiplier)
        pnl = (payoff - entry_price) * spec.quantity * 100
        scenarios.append(pnl)

    scenarios = np.array(scenarios)
    ev = np.mean(scenarios)
    win_rate = np.mean(scenarios > 0) * 100.0

    # Calculate Sharpe-like ratio (return/risk)
    std_pnl = np.std(scenarios)
    sharpe = ev / std_pnl if std_pnl > 0 else 0.0

    return {
        'scenarios': scenarios,
        'ev': ev,
        'win_rate': win_rate,
        'sharpe': sharpe
    }


# ── Kelly Criterion Position Sizing ──────────────────────────────────────────

def calculate_kelly_fraction(expected_return: float, volatility: float,
                           risk_free_rate: float = 0.05) -> float:
    """
    Calculate Kelly fraction for optimal position sizing.

    Kelly fraction = (expected_return - risk_free_rate) / volatility^2

    Args:
        expected_return: Expected annual return (e.g., 0.15 for 15%)
        volatility: Annual volatility (e.g., 0.25 for 25%)
        risk_free_rate: Risk-free rate (default 5%)

    Returns:
        Kelly fraction (0.0 to 1.0)
    """
    if volatility <= 0 or expected_return <= risk_free_rate:
        return 0.0

    excess_return = expected_return - risk_free_rate
    kelly_fraction = excess_return / (volatility ** 2)

    # Cap at reasonable maximum
    return min(kelly_fraction, 1.0)


def kelly_position_size(kelly_fraction: float, portfolio_value: float,
                       fractional_kelly: float = 0.35,
                       max_position_pct: float = 0.15) -> float:
    """
    Calculate position size using Kelly criterion with safety constraints.

    Args:
        kelly_fraction: Raw Kelly fraction from calculate_kelly_fraction
        portfolio_value: Total portfolio value
        fractional_kelly: Fraction of full Kelly to use (default 35%)
        max_position_pct: Maximum position as % of portfolio (default 15%)

    Returns:
        Position size in dollars
    """
    # Apply fractional Kelly for conservative sizing
    adjusted_kelly = kelly_fraction * fractional_kelly

    # Cap at maximum position percentage
    final_fraction = min(adjusted_kelly, max_position_pct)

    return final_fraction * portfolio_value


def enhanced_signal_sizing(signal_score: float, volatility: float,
                         portfolio_value: float, base_expected_return: float = 0.12) -> dict:
    """
    Enhanced position sizing combining signal strength with Kelly optimization.

    Args:
        signal_score: Signal strength (0-100)
        volatility: Estimated volatility for the asset
        portfolio_value: Total portfolio value
        base_expected_return: Base expected return assumption

    Returns:
        Dict with position sizing information
    """
    try:
        # Import config function with fallback
        try:
            from .dynamic_config import cfg
        except ImportError:
            # Fallback - use reasonable defaults
            def cfg(key, default=None):
                defaults = {
                    "kelly.fractional_kelly": 0.35,
                    "kelly.max_position_pct": 0.15,
                    "min_score_threshold": 63.0,
                    "position_scale_factor": 0.06,
                    "min_position_pct": 0.04
                }
                return defaults.get(key, default)
    except Exception:
        # Ultimate fallback
        def cfg(key, default=None):
            return default or 0.35 if "fractional" in key else 0.15

    # Scale expected return based on signal strength
    score_multiplier = max(0, (signal_score - 50) / 50)  # 0 at score 50, 1 at score 100
    expected_return = base_expected_return * (1 + score_multiplier)

    # Calculate Kelly fraction
    kelly_fraction = calculate_kelly_fraction(expected_return, volatility)

    # Get Kelly-based position size
    kelly_size = kelly_position_size(
        kelly_fraction,
        portfolio_value,
        cfg("kelly.fractional_kelly", 0.35),
        cfg("kelly.max_position_pct", 0.15)
    )

    # Traditional signal-based sizing for comparison
    min_threshold = cfg("min_score_threshold", 63.0)
    scale_factor = cfg("position_scale_factor", 0.06)
    min_pct = cfg("min_position_pct", 0.04)

    if signal_score < min_threshold:
        signal_size = 0
    else:
        excess_score = signal_score - min_threshold
        signal_pct = min_pct + (excess_score * scale_factor / 100)
        signal_size = signal_pct * portfolio_value

    # Use the more conservative of the two approaches
    final_size = min(kelly_size, signal_size) if signal_size > 0 else kelly_size

    return {
        "position_size": final_size,
        "kelly_fraction": kelly_fraction,
        "kelly_size": kelly_size,
        "signal_size": signal_size,
        "expected_return": expected_return,
        "volatility": volatility,
        "score_multiplier": score_multiplier,
        "sizing_method": "kelly_enhanced"
    }