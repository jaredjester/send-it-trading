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
    return float(np.exp(-r * T) * np.mean(payoffs))


# ── PnL distribution ───────────────────────────────────────────────────────────

def pnl_distribution(spec: OptionSpec, entry_price: float,
                     S: float, K: float, T: float, r: float, sigma: float,
                     n_paths: int = 10_000, transaction_cost: float = 0.65) -> np.ndarray:
    """Simulate full PnL per contract at expiry."""
    _, terminal = mc_price_gbm(spec.kind, S, K, T, r, sigma, n_paths)
    payoffs = np.maximum(terminal - K, 0) if spec.kind == 'call' else np.maximum(K - terminal, 0)
    pnl = (payoffs - entry_price) * 100 * spec.quantity - transaction_cost
    return pnl
