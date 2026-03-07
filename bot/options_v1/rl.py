"""Reinforcement learning feedback loop for continuous trade improvement."""
import os
import json
import logging
import numpy as np
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from pathlib import Path
from engine.core.trading_db import db

logger = logging.getLogger(__name__)

MEMORY_PATH  = Path(str(Path(os.getenv('STATE_DIR', str(Path(__file__).resolve().parent.parent.parent / 'engine' / 'state'))) / 'trade_memory.jsonl'))
WEIGHTS_PATH = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'rl_weights.json'))


# Signal IC constants
SIGNAL_TYPES  = ['news', 'insider', 'ca', 'polymarket']
IC_WINDOW     = 30
IC_STRONG     = 0.15
IC_WEAK       = 0.03
MAX_SIG_BOOST = 0.25


@dataclass

class TradeRecord:
    trade_id: str
    timestamp: str
    symbol: str
    strategy: str
    kind: str
    strike: float
    expiry_years: float
    contracts: int
    entry_price: float
    exit_price: Optional[float]
    pnl: Optional[float]
    ev_at_entry: float
    kelly_fraction: float
    delta: float
    gamma: float
    vega: float
    iv_at_entry: float
    spot_at_entry: float
    outcome: str = 'open'   # 'open' | 'win' | 'loss' | 'breakeven'
    context: Dict[str, Any] = field(default_factory=dict)


class RLTrainer:
    """
    Contextual bandit / policy gradient RL loop.
    - Records every trade with full context
    - Learns signal weights from outcomes
    - Adjusts Kelly fraction and EV threshold per strategy
    - Runs overnight mark-to-market reviews for continuous learning
    """

    def __init__(self):
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.weights = self._load_weights()

    def _load_weights(self) -> Dict:
        if WEIGHTS_PATH.exists():
            with open(WEIGHTS_PATH) as f:
                return json.load(f)
        return {
            'kelly_scale': {'VRP': 1.0, 'DCVX': 1.0},
            'ev_threshold': {'VRP': 0.0, 'DCVX': 0.0},
            'win_rate':     {'VRP': 0.5, 'DCVX': 0.5},
            'n_trades':     {'VRP': 0,   'DCVX': 0},
            'total_pnl':    {'VRP': 0.0, 'DCVX': 0.0},
        }

    def prune_stale_pending(self, max_age_days: int = 7):
        """Remove pending signals older than max_age_days."""
        import time as _t
        pending = self.weights.setdefault('pending_signals', {})
        now = _t.time()
        stale = [tid for tid, sig in pending.items()
                 if now - sig.get('entry_ts', now) > max_age_days * 86400]
        for tid in stale:
            logger.info('[RL] Pruning stale pending signal: %s (%s)',
                       tid[:8], pending[tid].get('symbol', '?'))
            del pending[tid]
        if stale:
            self._save_weights()
        return len(stale)

    def _load_memory(self) -> list:
        """Load trade memory records from DB for replay weighting."""
        return db.get_trades()

    def _save_weights(self):
        with open(WEIGHTS_PATH, 'w') as f:
            json.dump(self.weights, f, indent=2)

    def record_trade(self, record: TradeRecord):
        trade_dict = asdict(record)
        db.record_trade(trade_dict)
        logger.info('RL recorded trade %s %s %s', record.trade_id, record.symbol, record.strategy)

    def close_trade(self, trade_id: str, exit_price: float, pnl: float):
        """Mark a trade as closed and trigger weight update."""
        outcome = 'win' if pnl > 0 else ('loss' if pnl < 0 else 'breakeven')
        db.update_trade_pnl(trade_id, pnl, outcome)
        # Load the trade for weight update
        trades = db.get_trades()
        trade = next((t for t in trades if t['id'] == trade_id), None)
        if trade:
            # PnL-weighted replay: large P&L = more learning signal
            all_pnls = [abs(t.get('pnl') or 0) for t in trades if t.get('pnl') is not None]
            avg_abs = (sum(all_pnls) / len(all_pnls)) if all_pnls else 50.0
            pnl_weight = max(0.5, min(3.0, abs(pnl) / max(avg_abs, 1.0)))
            logger.info('[RL] PnL-weighted replay: |pnl|=%.2f avg=%.2f weight=%.2f',
                        abs(pnl), avg_abs, pnl_weight)
            self._update_weights(trade, scale=pnl_weight)
            # Activate IC learning — update signal quality scores from this outcome
            try:
                closed_rec = next((r for r in trades if r.get('trade_id') == trade_id or r.get('id') == trade_id), None)
                if closed_rec:
                    self.update_signal_ic(trade_id, pnl, closed_rec.get('strategy', 'DCVX'))
            except Exception as _ic_err:
                logger.warning('[RL] IC update failed: %s', _ic_err)

    def mark_to_market(self, trade_id: str, current_price: float):
        """
        Overnight learning: update RL weights from unrealized P&L without
        closing the trade. Uses a soft/dampened weight update (50% strength)
        so open positions don't overfit to mark-to-market noise.
        """
        if not MEMORY_PATH.exists():
            return
        with open(MEMORY_PATH) as f:
            for line in f:
                rec = json.loads(line)
                if rec['trade_id'] == trade_id and rec['outcome'] == 'open':
                    entry = rec.get('entry_price', 0)
                    contracts = rec.get('contracts', 1)
                    unrealized_pnl = (current_price - entry) * contracts * 100
                    # Soft update — half strength vs real close
                    soft_rec = dict(rec)
                    soft_rec['pnl'] = unrealized_pnl * 0.5
                    self._update_weights(soft_rec, scale=0.5)
                    logger.info(
                        'RL mark-to-market %s %s: entry=%.2f current=%.2f unrealized_pnl=%.2f (soft)',
                        rec.get('symbol'), rec.get('strategy'),
                        entry, current_price, unrealized_pnl
                    )
                    break

    def overnight_review(self, get_spot_fn) -> int:
        """
        Run for every open trade: fetch current spot price, approximate
        current option value (simplified), update RL via mark_to_market.
        Returns number of positions reviewed.
        """
        if not MEMORY_PATH.exists():
            logger.info('Overnight review: no trade memory found — skipping')
            return 0

        open_trades = []
        with open(MEMORY_PATH) as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                    if rec.get('outcome') == 'open':
                        open_trades.append(rec)
                except Exception:
                    continue

        if not open_trades:
            logger.info('Overnight review: no open trades to review')
            return 0

        reviewed = 0
        for rec in open_trades:
            sym = rec.get('symbol')
            entry_price = rec.get('entry_price', 0)
            kind = rec.get('kind', 'call')
            strike = rec.get('strike', 0)
            expiry_years = rec.get('expiry_years', 0.1)
            try:
                spot = get_spot_fn(sym)
                # Simplified intrinsic + time value approximation
                # (Black-Scholes would be ideal but needs IV; use intrinsic floor)
                if kind == 'call':
                    intrinsic = max(0, spot - strike)
                else:
                    intrinsic = max(0, strike - spot)
                # Add remaining time value estimate (rough: 20% of entry * remaining_frac)
                time_value = entry_price * 0.2 * max(0, expiry_years)
                current_price = intrinsic + time_value
                # Don't go below 5% of entry (options rarely go to 0 mid-life)
                current_price = max(entry_price * 0.05, current_price)

                self.mark_to_market(rec['trade_id'], current_price)
                reviewed += 1
                logger.info(
                    'Overnight review: %s %s %s strike=%.0f spot=%.2f → est_price=%.2f',
                    sym, kind, rec.get('strategy'), strike, spot, current_price
                )
            except Exception as e:
                logger.warning('Overnight review failed for %s: %s', sym, e)

        logger.info('Overnight review complete: %d/%d positions reviewed', reviewed, len(open_trades))
        return reviewed

    def _compute_risk_adjusted_reward(self, rec: Dict) -> float:
        """
        Risk-adjusted reward: penalize drawdown and transaction cost.
        reward = PnL - 0.1 * drawdown_from_peak - 0.05 * |entry_cost|
        This teaches the RL to avoid large drawdowns, not just maximize PnL.
        """
        pnl        = rec.get('pnl', 0.0) or 0.0
        entry      = rec.get('entry_price', 0.0) or 0.0
        contracts  = rec.get('contracts', 1) or 1
        entry_cost = entry * contracts * 100    # dollars paid to enter

        # Proxy drawdown: for losses, pnl IS the drawdown from entry
        drawdown = abs(pnl) if pnl < 0 else 0.0

        reward = pnl - 0.10 * drawdown - 0.05 * (entry_cost / 100)
        return round(reward, 4)

    def _update_weights(self, rec: Dict, scale: float = 1.0):
        strat = rec.get('strategy', 'VRP')
        if strat not in self.weights['n_trades']:
            for k in ['kelly_scale', 'ev_threshold', 'win_rate', 'n_trades', 'total_pnl']:
                self.weights[k][strat] = self.weights[k].get('VRP', 0)

        n   = self.weights['n_trades'][strat]
        pnl = rec.get('pnl', 0.0) or 0.0
        won = 1 if pnl > 0 else 0

        # Risk-adjusted reward (used for Kelly scaling decisions)
        reward = self._compute_risk_adjusted_reward(rec)
        logger.debug('[RL] risk_adj_reward=%.4f (pnl=%.2f)', reward, pnl)

        # Bayesian win rate update (dampened by scale for soft updates)
        effective_n = n + scale
        alpha = self.weights['win_rate'][strat] * n + (won * scale)
        self.weights['win_rate'][strat] = (alpha + 1) / (effective_n + 2)
        self.weights['n_trades'][strat] = n + scale
        self.weights['total_pnl'][strat] += pnl * scale

        # Kelly scale driven by RISK-ADJUSTED reward, not raw PnL
        # Positive reward → scale up; Negative reward → scale down harder
        reward_norm = reward / max(1.0, abs(pnl) + 0.01)   # normalize [-1, 1]
        if reward_norm > 0.1:
            self.weights['kelly_scale'][strat] = min(
                1.5, self.weights['kelly_scale'][strat] * (1 + 0.05 * scale * reward_norm))
        elif reward_norm < -0.1:
            self.weights['kelly_scale'][strat] = max(
                0.1, self.weights['kelly_scale'][strat] * (1 - 0.08 * scale * abs(reward_norm)))

        self._save_weights()
        logger.info('[RL] %s (scale=%.1f): reward=%.3f win_rate=%.2f kelly=%.2f total_pnl=%.2f',
                    strat, scale, reward, self.weights['win_rate'][strat],
                    self.weights['kelly_scale'][strat], self.weights['total_pnl'][strat])


    def record_signal_context(self, trade_id: str, symbol: str, strategy: str,
                               news_score: float = 0.0,
                               insider_score: float = 0.0,
                               ca_score: float = 0.0,
                               polymarket_score: float = 0.0,
                               direction: str = 'call'):
        """Store signal scores at trade entry for IC evaluation."""
        import time
        self.weights.setdefault('pending_signals', {})[trade_id] = {
            'symbol': symbol, 'strategy': strategy, 'direction': direction,
            'news_score': round(news_score, 3),
            'insider_score': round(insider_score, 3),
            'ca_score': round(ca_score, 3),
            'polymarket_score': round(polymarket_score, 3),
            'entry_ts': time.time(),
        }
        logger.info('[RL] Signal ctx %s (%s): news=%.3f insider=%.3f ca=%.3f poly=%.3f',
                          trade_id, symbol, news_score, insider_score, ca_score, polymarket_score)
        self._save_weights()

    def _recalc_ic(self):
        """Recompute IC for all signal types from observation window."""
        import math
        w = self.weights
        for sig_type in SIGNAL_TYPES:
            obs = w.get('ic_obs', {}).get(sig_type, [])
            if len(obs) < 3:
                continue
            ic = sum(obs) / len(obs)
            w.setdefault('signal_ic', {})[sig_type] = round(ic, 4)
            logger.info('[RL] IC %s: %.4f (n=%d)', sig_type, ic, len(obs))

    def update_signal_ic(self, trade_id: str, pnl: float, strategy: str):
        """Update IC observations from a closed trade outcome."""
        import math
        w   = self.weights
        ctx = w.get('pending_signals', {}).pop(trade_id, None)
        if not ctx:
            return
        outcome_dir = 1.0 if pnl > 0 else -1.0
        trade_dir   = 1.0 if ctx.get('direction') == 'call' else -1.0
        for sig_type in SIGNAL_TYPES:
            score = ctx.get(f'{sig_type}_score', 0.0)
            align = math.copysign(1, score) * trade_dir * outcome_dir if score != 0 else 0.0
            obs   = w.setdefault('ic_obs', {}).setdefault(sig_type, [])
            obs.append(round(align, 1))
            if len(obs) > IC_WINDOW:
                obs.pop(0)
        self._recalc_ic()

    def signal_kelly_adj(self, news_score: float = 0.0,
                          insider_score: float = 0.0,
                          ca_score: float = 0.0,
                          polymarket_score: float = 0.0) -> float:
        """Return Kelly multiplier (0.75..1.25) based on signal IC × current scores."""
        w   = self.weights
        adj = 0.0
        for sig_type, score in [('news', news_score), ('insider', insider_score), ('ca', ca_score), ('polymarket', polymarket_score)]:
            ic = w.get('signal_ic', {}).get(sig_type, 0.0)
            if abs(ic) < IC_WEAK:
                continue
            weight = min(abs(ic) / IC_STRONG, 1.0)
            adj   += weight * score * MAX_SIG_BOOST
        adj = max(-MAX_SIG_BOOST, min(MAX_SIG_BOOST, adj))
        return round(1.0 + adj, 3)

    def record_plan_outcome(self, plan_id: str, strategy: str,
                            target_hit: bool, actual_rr: float,
                            days_held: int, catalyst_window: int):
        """
        Learn from trade plan accuracy:
        - Target hit rate by strategy
        - R/R achievement vs planned
        - Catalyst window accuracy (time prediction quality)
        """
        w = self.weights
        w.setdefault('plan_stats', {}).setdefault(strategy, {
            'plans_total': 0,
            'targets_hit': 0,
            'stops_hit': 0,
            'avg_rr_achieved': 0.0,
            'avg_days_vs_window': 0.0,
            'window_accuracy': [],
        })
        ps = w['plan_stats'][strategy]
        ps['plans_total'] += 1

        if target_hit:
            ps['targets_hit'] += 1
        else:
            ps['stops_hit'] += 1

        # Track window accuracy (how well we predict timing)
        window_ratio = round(days_held / max(1, catalyst_window), 2)
        ps['window_accuracy'].append(window_ratio)
        if len(ps['window_accuracy']) > 30:
            ps['window_accuracy'].pop(0)
        ps['avg_days_vs_window'] = round(
            sum(ps['window_accuracy']) / len(ps['window_accuracy']), 2
        )

        # Rolling avg R/R
        n = ps['plans_total']
        ps['avg_rr_achieved'] = round(
            (ps['avg_rr_achieved'] * (n - 1) + actual_rr) / n, 3
        )

        logger.info(
            '[RL] Plan outcome: %s target_hit=%s rr=%.2f days=%d/%d (ratio=%.2f) '
            'cumulative: %d/%d targets hit (%.0f%%)',
            strategy, target_hit, actual_rr, days_held, catalyst_window,
            window_ratio, ps['targets_hit'], ps['plans_total'],
            ps['targets_hit'] / max(1, ps['plans_total']) * 100
        )

        # Adjust catalyst window recommendation based on learning
        if len(ps['window_accuracy']) >= 5:
            avg_ratio = ps['avg_days_vs_window']
            if avg_ratio < 0.4:
                # Plans resolve much faster than expected → shorten window
                w.setdefault('plan_params', {})['catalyst_window_adjust'] = -2
                logger.info('[RL] Catalyst window too long (avg ratio=%.2f) → suggesting shorter', avg_ratio)
            elif avg_ratio > 0.85:
                # Plans expire before resolving → lengthen window
                w.setdefault('plan_params', {})['catalyst_window_adjust'] = +3
                logger.info('[RL] Catalyst window too short (avg ratio=%.2f) → suggesting longer', avg_ratio)
            else:
                w.setdefault('plan_params', {})['catalyst_window_adjust'] = 0

        # Adjust target R/R if consistently not hitting 2:1
        if n >= 10:
            if ps['avg_rr_achieved'] < 0.5 and ps['targets_hit'] / n < 0.3:
                # Targets too ambitious — signal to lower them
                w.setdefault('plan_params', {})['target_rr_adjust'] = -0.5
                logger.info('[RL] Target R/R too ambitious (avg=%.2f, hit=%.0f%%) → suggesting lower',
                           ps['avg_rr_achieved'], ps['targets_hit']/n*100)

        self._save_weights()

    def get_plan_params(self) -> dict:
        """Return learned adjustments for trade planner."""
        return self.weights.get('plan_params', {})

    def record_plan_outcome(self, plan_id: str, target_hit: bool,
                            actual_rr: float, days_held: int,
                            catalyst_window: int, strategy: str):
        """
        Learn from plan accuracy:
        - Did the target get hit?
        - How long did it take vs expected?
        - What was the actual R/R vs planned?
        """
        w = self.weights
        w.setdefault('plan_stats', {}).setdefault(strategy, {
            'plans_total': 0, 'targets_hit': 0, 'stops_hit': 0,
            'avg_rr_achieved': 0.0, 'avg_days_vs_window': 0.0,
            'window_accuracy': [],
        })
        ps = w['plan_stats'][strategy]
        ps['plans_total'] += 1
        if target_hit:
            ps['targets_hit'] += 1
        else:
            ps['stops_hit'] = ps.get('stops_hit', 0) + 1
        window_ratio = days_held / max(1, catalyst_window)
        ps['window_accuracy'].append(round(window_ratio, 2))
        if len(ps['window_accuracy']) > 30:
            ps['window_accuracy'].pop(0)
        ps['avg_days_vs_window'] = sum(ps['window_accuracy']) / len(ps['window_accuracy'])
        ps['avg_rr_achieved'] = (
            ps['avg_rr_achieved'] * (ps['plans_total'] - 1) + actual_rr
        ) / ps['plans_total']

        logger.info('[RL] Plan outcome: %s target_hit=%s rr=%.2f days=%d/%d (window_ratio=%.2f)',
                   strategy, target_hit, actual_rr, days_held, catalyst_window, window_ratio)

        # Adjust catalyst_window default based on learning
        if len(ps['window_accuracy']) >= 5:
            avg_ratio = ps['avg_days_vs_window']
            if avg_ratio < 0.5:
                w.setdefault('plan_params', {})['catalyst_window_adjust'] = -1
            elif avg_ratio > 0.85:
                w.setdefault('plan_params', {})['catalyst_window_adjust'] = +2

        self._save_weights()

    def get_kelly_scale(self, strategy: str) -> float:
        return self.weights['kelly_scale'].get(strategy, 1.0)

    def get_ev_threshold(self, strategy: str) -> float:
        return self.weights['ev_threshold'].get(strategy, 0.0)

    def summary(self) -> Dict:
        return {
            'strategies': list(self.weights['n_trades'].keys()),
            'n_trades':   self.weights['n_trades'],
            'total_pnl':  self.weights['total_pnl'],
            'win_rates':  self.weights['win_rate'],
            'kelly_scales': self.weights['kelly_scale'],
        }
