"""Strategy modules: Volatility Risk Premium + Directional Convex.
Extensive logging at every decision point for full auditability."""
import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional, List
from .pricing import bs_price, bs_greeks, mc_price_gbm, pnl_distribution, OptionSpec
from .kelly import compute_kelly, position_size
from .data import market_data_bundle, get_risk_free_rate

logger = logging.getLogger(__name__)

SEP = '─' * 60


@dataclass
class Signal:
    strategy: str
    symbol: str
    action: str          # 'buy' | 'sell' | 'skip'
    kind: str            # 'call' | 'put'
    strike: float
    expiry_str: str
    expiry_years: float
    contracts: int
    entry_price: float
    ev: float
    kelly_fraction: float
    delta: float
    gamma: float
    vega: float
    theta: float
    reason: str


class VolRiskPremiumStrategy:
    """Sells short-dated ATM puts when IV significantly exceeds realized vol."""
    MIN_IV_PREMIUM = 0.05

    def scan(self, symbols: List[str], capital: float,
             expiry_years: float = 0.05) -> List[Signal]:
        signals = []
        rf = get_risk_free_rate()
        logger.info('%s VRP SCAN | %d symbols | capital=%.2f rf=%.3f expiry=%.0fd %s',
                    SEP, len(symbols), capital, rf, expiry_years * 365, SEP)
        for sym in symbols:
            try:
                bundle = market_data_bundle(sym)
                S, iv, chain = bundle['spot'], bundle['iv'], bundle['chain']
                rv = iv

                logger.info('[VRP] %s | S=%.2f IV=%.3f RV=%.3f premium=%.3f (need >%.3f)',
                            sym, S, iv, rv, iv - rv, self.MIN_IV_PREMIUM)

                if iv - rv < self.MIN_IV_PREMIUM:
                    logger.info('[VRP] %s → SKIP: IV premium too small (%.3f < %.3f)',
                                sym, iv - rv, self.MIN_IV_PREMIUM)
                    continue

                step = 5 if S >= 200 else 1
                K = round(S / step) * step
                entry = bs_price('put', S, K, expiry_years, rf, iv)
                greeks = bs_greeks('put', S, K, expiry_years, rf, iv)
                spec = OptionSpec(kind='put', strike=K, expiry=expiry_years, quantity=-1)
                pnl = pnl_distribution(spec, entry, S, K, expiry_years, rf, iv)
                kelly = compute_kelly(pnl, capital)
                contracts = position_size(capital, kelly, entry)

                logger.info('[VRP] %s | K=%.0f entry=%.3f | EV=%.3f win_rate=%.1f%% '
                            'kelly_full=%.4f kelly_frac=%.4f sharpe=%.2f approved=%s',
                            sym, K, entry, kelly.expected_value,
                            kelly.win_rate * 100, kelly.full_kelly,
                            kelly.fractional_kelly, kelly.sharpe, kelly.approved)

                if kelly.approved:
                    logger.info('[VRP] %s → SIGNAL: SELL PUT K=%.0f x%d @ %.3f EV=%.3f',
                                sym, K, contracts, entry, kelly.expected_value)
                else:
                    logger.info('[VRP] %s → SKIP: Kelly rejected (EV=%.3f win_rate=%.1f%%)',
                                sym, kelly.expected_value, kelly.win_rate * 100)

                signals.append(Signal(
                    strategy='VRP', symbol=sym,
                    action='sell' if kelly.approved else 'skip',
                    kind='put', strike=K, expiry_str='', expiry_years=expiry_years,
                    contracts=contracts, entry_price=entry,
                    ev=kelly.expected_value, kelly_fraction=kelly.fractional_kelly,
                    delta=greeks['delta'], gamma=greeks['gamma'],
                    vega=greeks['vega'], theta=greeks['theta'],
                    reason='IV={:.2f} RV={:.2f} premium={:.2f}'.format(iv, rv, iv - rv),
                ))
            except Exception as e:
                logger.error('[VRP] %s → ERROR: %s', sym, e)
        logger.info('[VRP] Scan complete: %d signals (%d approved)',
                    len(signals), sum(1 for s in signals if s.action != 'skip'))
        return signals


class DirectionalConvexStrategy:
    """Buys OTM calls or puts when a directional signal fires with positive EV."""
    OTM_OFFSET = 0.02   # 2% OTM — near-the-money, delta ~0.4-0.5
    CAPITAL_FLOOR = 350  # Below this, push OTM to find cheaper contracts

    def scan(self, symbols: List[str], capital: float,
             direction: str = 'call', expiry_years: float = 0.1) -> List[Signal]:
        signals = []
        rf = get_risk_free_rate()
        logger.info('%s DCVX SCAN | direction=%s | %d symbols | capital=%.2f '
                    'rf=%.3f expiry=%.0fd %s',
                    SEP, direction.upper(), len(symbols), capital,
                    rf, expiry_years * 365, SEP)

        for sym in symbols:
            try:
                bundle = market_data_bundle(sym)
                S, iv = bundle['spot'], bundle['iv']

                # Dynamic OTM: push further OTM when capital is tight
                _offset = self.OTM_OFFSET
                if capital < self.CAPITAL_FLOOR:
                    _offset = min(0.08, self.OTM_OFFSET + 0.03)  # up to 8% OTM for cheaper premium
                    logger.debug('[DCVX] Low capital ($%.0f) — OTM offset widened to %.0f%%', capital, _offset * 100)
                if direction == 'call':
                    K = S * (1 + _offset)
                else:
                    K = S * (1 - _offset)
                # Strike increments: $1 for sub-$200 stocks, $5 for high-priced
                step = 5 if S >= 200 else 1
                K = round(K / step) * step

                logger.info('[DCVX] %s | S=%.2f IV=%.3f | target K=%.0f (%.0f%% OTM)',
                            sym, S, iv, K, self.OTM_OFFSET * 100)

                entry  = bs_price(direction, S, K, expiry_years, rf, iv)
                greeks = bs_greeks(direction, S, K, expiry_years, rf, iv)
                spec   = OptionSpec(kind=direction, strike=K,
                                    expiry=expiry_years, quantity=1)
                pnl    = pnl_distribution(spec, entry, S, K, expiry_years, rf, iv)
                kelly  = compute_kelly(pnl, capital)
                contracts = position_size(capital, kelly, entry)

                logger.info('[DCVX] %s | %s K=%.0f entry=%.4f | '
                            'delta=%.3f gamma=%.5f vega=%.4f theta=%.4f',
                            sym, direction.upper(), K, entry,
                            greeks['delta'], greeks['gamma'],
                            greeks['vega'], greeks['theta'])
                logger.info('[DCVX] %s | EV=%.4f win_rate=%.1f%% sharpe=%.2f | '
                            'kelly_full=%.4f kelly_frac=%.4f → %d contracts | approved=%s',
                            sym, kelly.expected_value, kelly.win_rate * 100,
                            kelly.sharpe, kelly.full_kelly, kelly.fractional_kelly,
                            contracts, kelly.approved)

                if kelly.approved:
                    logger.info('[DCVX] %s → SIGNAL: BUY %s K=%.0f x%d @ %.4f '
                                'EV=%.4f delta=%.3f',
                                sym, direction.upper(), K, contracts,
                                entry, kelly.expected_value, greeks['delta'])
                else:
                    reasons = []
                    if kelly.expected_value <= 0:
                        reasons.append('negative EV ({:.4f})'.format(kelly.expected_value))
                    if kelly.win_rate < 0.25:  # matches kelly.MIN_WIN_RATE
                        reasons.append('low win_rate ({:.1f}% < 25% min)'.format(kelly.win_rate * 100))
                    logger.info('[DCVX] %s → SKIP: %s',
                                sym, ' + '.join(reasons) or 'Kelly rejected')

                signals.append(Signal(
                    strategy='DCVX', symbol=sym,
                    action='buy' if kelly.approved else 'skip',
                    kind=direction, strike=K, expiry_str='',
                    expiry_years=expiry_years, contracts=contracts,
                    entry_price=entry, ev=kelly.expected_value,
                    kelly_fraction=kelly.fractional_kelly,
                    delta=greeks['delta'], gamma=greeks['gamma'],
                    vega=greeks['vega'], theta=greeks['theta'],
                    reason='Directional {} EV={:.4f} winrate={:.1f}%'.format(
                        direction, kelly.expected_value, kelly.win_rate * 100),
                ))
            except Exception as e:
                logger.error('[DCVX] %s → ERROR: %s', sym, e, exc_info=True)

        approved = [s for s in signals if s.action != 'skip']
        logger.info('[DCVX] Scan complete: %d/%d signals approved',
                    len(approved), len(signals))
        if approved:
            for s in approved:
                logger.info('[DCVX] ✓ %s %s K=%.0f x%d @ %.4f EV=%.4f',
                            s.symbol, s.kind.upper(), s.strike,
                            s.contracts, s.entry_price, s.ev)
        return signals
