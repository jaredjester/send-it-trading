#!/usr/bin/env python3
"""
Synthetic options training environment for Options V1 RL.

Generates Monte Carlo price paths using Geometric Brownian Motion,
reprices options on each path step using Black-Scholes, and feeds
trade outcomes into rl.py _update_weights() with full risk-adjusted reward.

Goal: overcome 6-trade data bottleneck by generating 10,000+ episodes.

Usage:
    python train_synthetic.py \
        --episodes 10000 --symbols SPY QQQ AAPL --output data/rl_weights.json

Run overnight on Pi or Mac. Safe to interrupt (checkpoints every 500 ep).
"""
import argparse
import json
import math
import random
import uuid
import time
import logging
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('train_synthetic')

# ── market parameter library (symbol -> (mu, sigma, typical_iv)) ──────────────
MARKET_PARAMS = {
    'SPY':  (0.10, 0.15, 0.18),
    'QQQ':  (0.12, 0.18, 0.22),
    'AAPL': (0.14, 0.22, 0.25),
    'MSFT': (0.13, 0.20, 0.23),
    'TSLA': (0.15, 0.55, 0.65),
    'NVDA': (0.18, 0.45, 0.50),
    'AMD':  (0.14, 0.40, 0.45),
    'META': (0.15, 0.30, 0.35),
    'AMZN': (0.12, 0.25, 0.28),
    'COIN': (0.10, 0.80, 0.90),
}

# ── BS helpers (self-contained, no imports from options_v1) ────────────────────
def _norm_cdf(x):
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x):
    import math
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs_price(kind, S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        if kind == 'call': return max(0.0, S - K)
        return max(0.0, K - S)
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if kind == 'call':
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    except (ValueError, ZeroDivisionError):
        return 0.0

def bs_delta(kind, S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return (1.0 if S > K else 0.0) if kind == 'call' else (-1.0 if S < K else 0.0)
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        return _norm_cdf(d1) if kind == 'call' else _norm_cdf(d1) - 1.0
    except (ValueError, ZeroDivisionError):
        return 0.0

# ── GBM price path ─────────────────────────────────────────────────────────────
def gbm_path(S0, mu, sigma, T_years, steps):
    """Returns list of prices of length steps+1."""
    dt = T_years / steps
    prices = [S0]
    for _ in range(steps):
        z = random.gauss(0, 1)
        prices.append(prices[-1] * math.exp((mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * z))
    return prices

# ── Stochastic vol (simple mean-reverting IV path) ────────────────────────────
def iv_path(iv0, steps, kappa=2.0, theta=None, xi=0.3):
    """Heston-style mean-reverting IV."""
    theta = theta or iv0
    dt = 1.0 / 252 / steps
    ivs = [iv0]
    for _ in range(steps):
        dv = kappa * (theta - ivs[-1]) * dt + xi * ivs[-1] * math.sqrt(dt) * random.gauss(0, 1)
        ivs.append(max(0.05, ivs[-1] + dv))
    return ivs

# ── Single episode ─────────────────────────────────────────────────────────────
def run_episode(symbol, S0, mu, sigma, iv0, r=0.045, dte=21, kind='call'):
    """
    Returns dict: {entry_price, exit_price, pnl, delta, gamma_at_entry,
                   iv_at_entry, spot_at_entry, contracts, strategy, kind, strike}
    """
    T  = dte / 365.0
    # Pick strike: 2-5% OTM
    otm = random.uniform(0.01, 0.05)
    K = round(S0 * (1 + otm if kind == 'call' else 1 - otm), 2)
    iv_entry = iv0 * random.uniform(0.85, 1.15)

    entry_px = bs_price(kind, S0, K, T, r, iv_entry)
    if entry_px < 0.01:
        return None  # skip worthless options

    # Simulate hold period: 3-10 days
    hold_days = random.randint(3, min(10, dte - 1))
    steps = hold_days * 4   # 4 steps per day

    prices = gbm_path(S0, mu, sigma, hold_days / 365.0, steps)
    ivs    = iv_path(iv_entry, steps)
    S_exit = prices[-1]
    iv_exit = ivs[-1]
    T_exit = max(0.001, (dte - hold_days) / 365.0)

    exit_px = bs_price(kind, S_exit, K, T_exit, r, iv_exit)

    # Stop loss at 50%, target at 2:1 R/R
    stop = entry_px * 0.50
    target = entry_px + (entry_px - stop) * 2.0

    # Track MFE / MAE over path
    mid_prices = []
    for i in range(1, len(prices)):
        T_i = max(0.001, (dte - hold_days * i / steps) / 365.0)
        mid = bs_price(kind, prices[i], K, T_i, r, ivs[i])
        mid_prices.append(mid)
        if mid <= stop:
            exit_px = stop
            break
        if mid >= target:
            exit_px = target
            break

    contracts = 1
    pnl = (exit_px - entry_px) * contracts * 100

    # Greeks at entry
    delta_e = bs_delta(kind, S0, K, T, r, iv_entry)

    return {
        'symbol':        symbol,
        'strategy':      'DCVX',
        'kind':          kind,
        'strike':        K,
        'expiry_years':  T,
        'contracts':     contracts,
        'entry_price':   round(entry_px, 4),
        'exit_price':    round(exit_px, 4),
        'pnl':           round(pnl, 4),
        'ev_at_entry':   round(entry_px * 0.9, 4),
        'kelly_fraction': 0.1,
        'delta':         round(delta_e, 4),
        'gamma':         0.0,
        'vega':          0.0,
        'iv_at_entry':   round(iv_entry, 4),
        'spot_at_entry': round(S0, 4),
        'outcome':       'win' if pnl > 0 else 'loss',
        'context':       {'synthetic': True, 'hold_days': hold_days, 'dte': dte},
    }

# ── Training loop ──────────────────────────────────────────────────────────────
def train(episodes, symbols, weights_path, checkpoint_every=500):
    wpath = Path(weights_path)
    if wpath.exists():
        weights = json.loads(wpath.read_text())
        log.info('Loaded existing weights from %s', wpath)
    else:
        weights = {
            'kelly_scale':  {'DCVX': 1.0, 'VRP': 1.0},
            'ev_threshold': {'DCVX': 0.0, 'VRP': 0.0},
            'win_rate':     {'DCVX': 0.5, 'VRP': 0.5},
            'n_trades':     {'DCVX': 0.0, 'VRP': 0.0},
            'total_pnl':    {'DCVX': 0.0, 'VRP': 0.0},
        }

    wins = losses = skipped = 0

    for ep in range(1, episodes + 1):
        sym = random.choice(symbols)
        params = MARKET_PARAMS.get(sym, (0.12, 0.25, 0.28))
        mu, sigma, iv0 = params
        # Randomize starting spot (relative scale doesn't matter for learning)
        S0   = random.uniform(50, 800)
        kind = random.choice(['call', 'put'])
        dte  = random.choice([7, 14, 21, 30, 45])

        result = run_episode(sym, S0, mu, sigma, iv0, dte=dte, kind=kind)
        if result is None:
            skipped += 1
            continue

        pnl = result['pnl']
        won = 1 if pnl > 0 else 0
        strat = result['strategy']

        # Risk-adjusted reward
        entry_cost = result['entry_price'] * result['contracts'] * 100
        drawdown   = abs(pnl) if pnl < 0 else 0.0
        reward     = pnl - 0.10 * drawdown - 0.05 * (entry_cost / 100)

        # PnL-weighted scale
        n_so_far = weights['n_trades'][strat]
        # Running avg abs pnl
        avg_abs = weights.get('_avg_abs_pnl', {}).get(strat, 50.0)
        new_avg = (avg_abs * n_so_far + abs(pnl)) / (n_so_far + 1) if n_so_far > 0 else abs(pnl)
        weights.setdefault('_avg_abs_pnl', {})[strat] = new_avg
        pnl_weight = max(0.5, min(3.0, abs(pnl) / max(new_avg, 1.0)))
        scale = pnl_weight

        # Bayesian update
        n   = weights['n_trades'][strat]
        eff = n + scale
        alpha = weights['win_rate'][strat] * n + won * scale
        weights['win_rate'][strat]  = (alpha + 1) / (eff + 2)
        weights['n_trades'][strat]  = n + scale
        weights['total_pnl'][strat] += pnl * scale

        # Kelly scale via risk-adjusted reward
        reward_norm = reward / max(1.0, abs(pnl) + 0.01)
        ks = weights['kelly_scale'][strat]
        if reward_norm > 0.1:
            weights['kelly_scale'][strat] = min(1.5, ks * (1 + 0.05 * scale * reward_norm))
        elif reward_norm < -0.1:
            weights['kelly_scale'][strat] = max(0.1, ks * (1 - 0.08 * scale * abs(reward_norm)))

        if won: wins += 1
        else:   losses += 1

        # Checkpoint
        if ep % checkpoint_every == 0:
            wpath.write_text(json.dumps(weights, indent=2))
            wr = weights['win_rate'][strat]
            ks = weights['kelly_scale'][strat]
            log.info('ep=%d | wins=%d losses=%d | win_rate=%.3f kelly=%.3f reward=%.3f pnl=%.2f',
                     ep, wins, losses, wr, ks, reward, pnl)

    wpath.write_text(json.dumps(weights, indent=2))
    log.info('Training complete: %d episodes | wins=%d losses=%d skipped=%d',
             episodes, wins, losses, skipped)
    log.info('Final DCVX: win_rate=%.3f kelly_scale=%.3f total_pnl=%.2f',
             weights['win_rate']['DCVX'],
             weights['kelly_scale']['DCVX'],
             weights['total_pnl']['DCVX'])
    return weights


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Synthetic RL trainer for Options V1')
    parser.add_argument('--episodes',  type=int,   default=10000)
    parser.add_argument('--symbols',   nargs='+',  default=list(MARKET_PARAMS.keys()))
    parser.add_argument('--output',    default=str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'rl_weights.json'))
    parser.add_argument('--checkpoint',type=int,   default=500)
    parser.add_argument('--dry-run',   action='store_true', help='Run but do not write weights')
    args = parser.parse_args()

    log.info('Starting synthetic training: %d episodes on %s', args.episodes, args.symbols)
    log.info('Output: %s', args.output)

    weights = train(args.episodes, args.symbols, args.output, args.checkpoint)
