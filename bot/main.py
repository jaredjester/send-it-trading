#!/usr/bin/env python3
import signal as _signal
import threading as _threading
"""
Options V1 — Master Orchestrator
Pipeline:
  1. Pull symbols from live Alpaca watchlist ("Options V1")
  2. Pre-filter: Alpaca-optionable + CA risk check w/ FinBERT sentiment
  3. Scan with Directional Convex strategy
  4. EV filter + Kelly sizing (RL-adapted, CA sentiment boosts/cuts Kelly)
  5. Greek risk check
  6. Execute approved trades via Alpaca
  7. Record to RL memory (includes CA sentiment in trade context)
  8. Overnight: 20-min prep loop — RL review, chain cache, FinBERT pre-score
"""
import os
from pathlib import Path
import sys
import time
import uuid
import logging
import requests
from datetime import datetime, timezone

BOT_DIR = Path(__file__).resolve().parent
REPO_DIR = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(REPO_DIR))

import alpaca_env
alpaca_env.bootstrap()

from options_v1.data       import market_data_bundle, get_risk_free_rate, get_spot, get_option_chain, get_account, get_positions, get_option_snapshot
from options_v1.pricing    import bs_price, bs_greeks, pnl_distribution, OptionSpec
from options_v1.kelly      import compute_kelly, position_size
from options_v1.risk       import RiskManager, GreekLimits, Position
from options_v1.strategies import DirectionalConvexStrategy, Signal  # VRP removed (sells puts, requires margin)
from options_v1.rl         import RLTrainer, TradeRecord
from options_v1.execution  import (
    submit_option_order, build_occ_symbol, close_position,
    get_market_quote, submit_gtc_exit_order, verify_position_closed,
    get_open_orders, cancel_order
)
from options_v1.trade_planner import create_plan, save_plan, load_open_plans, close_plan, update_plan_oc
from options_v1.opportunity_cost import OpportunityCostEngine
from options_v1.watchlist  import WatchlistManager
from options_v1.calendar   import filter_corporate_action_risks
from options_v1.synthetic_pricing import SyntheticPricer
from options_v1.news_scanner       import run_news_scan, get_vix
from options_v1.dynamic_watchlist  import run_dynamic_update, get_dynamic_status
from options_v1.gamma_scanner      import get_scanner as get_gamma_scanner, scan_and_alert as gamma_scan_alert, GammaProfile
from options_v1             import insider_scanner
from options_v1             import polymarket_scanner
try:
    from options_v1 import telegram_alerts as _tg
except Exception as _e:
    logging.getLogger('bot').warning('Telegram alerts unavailable: %s', _e)
    _tg = None

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE = str(Path(os.getenv('LOG_DIR', str(BOT_DIR.parent / 'engine' / 'logs'))) / 'bot.log')
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger('main')

# ── Config ─────────────────────────────────────────────────────────────────────
import signal as _signal
import threading as _threading
_shutdown_event = _threading.Event()
_consecutive_errors = 0

def _on_shutdown(signum, frame):
    logger.warning('[SHUTDOWN] Signal %d received — completing current cycle then exiting cleanly', signum)
    _shutdown_event.set()

_signal.signal(_signal.SIGTERM, _on_shutdown)
_signal.signal(_signal.SIGINT,  _on_shutdown)


PAPER_MODE            = os.getenv('PAPER_MODE', 'true').lower() == 'true'
CYCLE_SECS            = 1800   # 30 min during market hours
OVERNIGHT_CYCLE_SECS  = 600    # 10 min overnight prep loop
OVERNIGHT_LEARN_SECS  = 7200   # RL mark-to-market interval (every 2h)
OPEN_WARMUP_SECS      = 300    # wake 5 min before open

# CA sentiment thresholds
CA_SENTIMENT_BOOST    = 0.15
CA_SENTIMENT_CUT      = 0.20

risk_manager = RiskManager(GreekLimits(delta=50, gamma=20, vega=500, theta=-200))
rl           = RLTrainer()
dcvx_strat   = DirectionalConvexStrategy()
watchlist    = WatchlistManager()
synthetic    = SyntheticPricer()
oc_engine    = OpportunityCostEngine()

# ── Alpaca options pre-filter ──────────────────────────────────────────────────
_options_cache: dict = {}

# Sentinel to distinguish "confirmed no options" from "API error"
_NO_OPTIONS = 'no_options'

def has_alpaca_options(symbol: str):
    """Returns True/False on success, None on API error (don't remove on error)."""
    try:
        chain = get_option_chain(symbol)
        return len(chain) > 0
    except Exception as e:
        logger.warning('has_alpaca_options(%s): API error — keeping in watchlist: %s', symbol, e)
        return None   # error sentinel — do NOT remove

def filter_optionable(symbols: list) -> list:
    result = []
    for sym in symbols:
        cached = _options_cache.get(sym)
        if cached is None:  # not in cache at all
            result_check = has_alpaca_options(sym)
            if result_check is True:
                _options_cache[sym] = True
                result.append(sym)
            elif result_check is False:
                # Confirmed empty chain — safe to remove permanently
                _options_cache[sym] = _NO_OPTIONS
                logger.info('No Alpaca options for %s — removing from watchlist', sym)
                watchlist.remove(sym)
            else:
                # API error — keep in watchlist this cycle, don't cache
                logger.warning('filter_optionable: API error for %s — keeping this cycle', sym)
                result.append(sym)
        elif cached == _NO_OPTIONS:
            logger.debug('filter_optionable: %s cached as no-options — skipping', sym)
        else:
            result.append(sym)
    if not result:
        logger.warning('filter_optionable: zero passed — using raw list as fallback')
        return symbols
    logger.info('Options filter: %d/%d passed', len(result), len(symbols))
    return result


# ── Finviz signal bus ──────────────────────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))

def get_finviz_signals(base_symbols: list):
    import json as _json, os as _os, time as _time
    bus_path = str(Path(__file__).resolve().parent.parent / 'state/latest_signals.json')
    try:
        if _os.path.exists(bus_path):
            age = _time.time() - _os.path.getmtime(bus_path)
            if age < 1800:
                with open(bus_path) as _f:
                    data = _json.load(_f)
                hits = [h for h in data.get('signals', []) if h.get('symbol') in base_symbols]
                if hits:
                    logger.info('Signal bus: %d signals match watchlist (age=%.0fs)', len(hits), age)
                    return [(h['symbol'], 'call', h.get('score', 60), h.get('reason', '')) for h in hits]
    except Exception:
        pass
    return [(s, 'call', 60, 'watchlist') for s in base_symbols]


# ── Market status ──────────────────────────────────────────────────────────────
def is_market_open() -> bool:
    try:
        r = requests.get(
            'https://api.alpaca.markets/v2/clock',
            headers={
                'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
                'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
            },
            timeout=5,
        )
        return r.json().get('is_open', False)
    except Exception:
        return False


def get_capital() -> float:
    try:
        r = requests.get(
            'https://api.alpaca.markets/v2/account',
            headers={
                'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
                'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
            },
            timeout=5,
        )
        data = r.json()
        return float(data.get('cash', data.get('portfolio_value', 100.0)))
    except Exception:
        return 100.0


def get_account_state() -> dict:
    """Single call returning capital + options_buying_power."""
    try:
        r = requests.get(
            'https://api.alpaca.markets/v2/account',
            headers={
                'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
                'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
            },
            timeout=5,
        )
        data = r.json()
        cash = float(data.get('cash', 100.0))
        opts_bp = float(data.get('options_buying_power') or cash)
        equity  = float(data.get('portfolio_value', cash))
        return {'capital': cash, 'options_buying_power': opts_bp, 'equity': equity}
    except Exception as e:
        logger.warning('get_account_state failed: %s', e)
        return {'capital': 100.0, 'options_buying_power': 100.0, 'equity': 100.0}

def get_options_bp() -> float:
    return get_account_state()[options_buying_power]


# ── Trade execution ────────────────────────────────────────────────────────────
def execute_signal(sig: Signal, capital: float, ca_sentiment: dict = None, news_score: float = 0.0, insider_score: float = 0.0, polymarket_score: float = 0.0, options_bp: float = 0.0):
    logger.info('-- EXECUTE %s %s %s | K=%.0f x%d | entry=%.4f EV=%.4f delta=%.3f',
                sig.action.upper(), sig.strategy, sig.symbol,
                sig.strike, sig.contracts, sig.entry_price, sig.ev, sig.delta)
    if sig.action == 'skip' or sig.contracts <= 0:
        logger.info('   -> SKIP (action=%s contracts=%d)', sig.action, sig.contracts)
        return False

    rl_scale = rl.get_kelly_scale(sig.strategy)
    logger.info('   RL: kelly_scale=%.3f | n_trades=%d | win_rate=%.1f%%',
                rl_scale,
                rl.weights['n_trades'].get(sig.strategy, 0),
                rl.weights['win_rate'].get(sig.strategy, 0.5) * 100)

    # Signal IC-weighted Kelly adjustment (news + insider + CA)
    ca_score = 0.0
    if ca_sentiment:
        raw_ca = ca_sentiment.get('score', 0.0)
        ca_conf = ca_sentiment.get('confidence', 0.0)
        ca_score = raw_ca if ca_conf > 0.4 else 0.0

    sentiment_adj = rl.signal_kelly_adj(
        news_score=news_score,
        insider_score=insider_score,
        ca_score=ca_score,
        polymarket_score=polymarket_score,
    ) - 1.0   # convert multiplier to additive delta
    logger.info('   Signals: news=%.3f insider=%.3f ca=%.3f poly=%.3f → kelly_adj=%+.3f',
                news_score, insider_score, ca_score, polymarket_score, sentiment_adj)

    rl_scale  = max(0.1, rl_scale * (1 + sentiment_adj))
    contracts = max(1, int(sig.contracts * rl_scale))
    contracts = risk_manager.scale_for_limits(sig.delta, sig.gamma, sig.vega, contracts)
    if contracts <= 0:
        logger.warning('   -> BLOCKED by Greek risk limits (contracts=0 after scaling)')
        return False

    if sig.ev < rl.get_ev_threshold(sig.strategy):
        logger.info('   -> BLOCKED by RL EV threshold: EV=%.2f < min=%.2f',
                   sig.ev, rl.get_ev_threshold(sig.strategy))
        return False

    now     = datetime.now(timezone.utc)
    days    = int(sig.expiry_years * 365)
    from datetime import timedelta
    exp_dt  = now + timedelta(days=days)
    occ_sym = build_occ_symbol(sig.symbol, exp_dt.strftime('%y%m%d'), sig.kind, sig.strike)

    trade_id = str(uuid.uuid4())
    logger.info('TRADE: %s %s %s x%d @ %.2f EV=%.2f Kelly=%.3f sentiment_adj=%.2f | id=%s',
                sig.action.upper(), occ_sym, sig.strategy, contracts,
                sig.entry_price, sig.ev, sig.kelly_fraction * rl_scale, sentiment_adj, trade_id[:8])

    if not PAPER_MODE:
        try:
            # Fetch real market ask — use it as limit price instead of B-S theoretical
            quote = get_market_quote(occ_sym)
            if quote['ok']:
                real_ask = quote['ask']
                real_mid = quote['mid']
                theoretical = sig.entry_price
                # If ask > 2.0x theoretical, market is wildly overpriced — skip
                if real_ask > theoretical * 2.0:
                    logger.warning('   -> SKIP: market overpriced | ask=%.2f vs theoretical=%.2f (%.1fx)',
                                   real_ask, theoretical, real_ask / max(theoretical, 0.01))
                    return False
                # Bid-anchored limit: stay close to bid to capture spread edge.
                # Wide-spread options are illiquid — be patient, let sellers come to us.
                #   tight  (<10%): bid + 30% — still reasonable fill odds
                #   medium (10-30%): bid + 20% — lean toward bid
                #   wide   (>30%): bid + 15% — very patient, max edge capture
                bid        = quote['bid']
                spread     = real_ask - bid
                spread_pct = spread / max(real_mid, 0.01)
                if spread_pct < 0.10:
                    limit_px  = round(bid + spread * 0.30, 2)
                    placement = 'bid+30% (tight spread)'
                elif spread_pct < 0.30:
                    limit_px  = round(bid + spread * 0.20, 2)
                    placement = 'bid+20% (medium spread)'
                else:
                    limit_px  = round(bid + spread * 0.15, 2)
                    placement = 'bid+15% (wide spread — patient)'
                # Floor: never bid below bid itself (in case of rounding)
                limit_px  = max(round(bid + 0.01, 2), limit_px)
                logger.info('   Quote: bid=%.2f ask=%.2f spread=%.1f%% | limit=%.2f [%s]',
                            bid, real_ask, spread_pct * 100, limit_px, placement)
            else:
                # Fallback to theoretical + 2% if snapshot unavailable
                limit_px = round(sig.entry_price * 1.02, 2)
                logger.warning('   No live quote — using theoretical limit=%.2f', limit_px)
            # BP check: cost = limit_px × 100 shares × n_contracts
            order_cost = limit_px * 100 * contracts
            effective_bp = options_bp if options_bp > 0 else capital
            if order_cost > effective_bp * 1.02:   # 2% slack
                logger.warning('   -> SKIP: order cost $%.2f exceeds options_buying_power $%.2f',
                               order_cost, effective_bp)
                return False
            order    = submit_option_order(
                symbol=occ_sym, contracts=contracts, side=sig.action,
                order_type='limit', limit_price=limit_px,
            )
            order_id = order.get('id', 'unknown')
        except Exception as e:
            logger.error('   -> ORDER FAILED: %s', e)
            return False
    else:
        order_id = 'paper-{}'.format(uuid.uuid4().hex[:8])
        logger.info('[PAPER] Would submit: %s x%d %s', occ_sym, contracts, sig.action)

    rl.record_trade(TradeRecord(
        trade_id=trade_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=sig.symbol, strategy=sig.strategy,
        kind=sig.kind, strike=sig.strike, expiry_years=sig.expiry_years,
        contracts=contracts, entry_price=sig.entry_price,
        exit_price=None, pnl=None,
        ev_at_entry=sig.ev, kelly_fraction=sig.kelly_fraction * rl_scale,
        delta=sig.delta, gamma=sig.gamma, vega=sig.vega,
        iv_at_entry=0.0, spot_at_entry=0.0,
        outcome='open',
        context={
            'order_id': order_id,
            'paper': PAPER_MODE,
            'ca_sentiment': ca_sentiment,
            'sentiment_adj': sentiment_adj,
        },
    ))
    risk_manager.add_position(Position(
        symbol=sig.symbol, kind=sig.kind, strike=sig.strike,
        expiry=sig.expiry_years, quantity=contracts,
        delta=sig.delta, gamma=sig.gamma, vega=sig.vega,
        theta=sig.theta, entry_price=sig.entry_price,
    ))
    logger.info('   -> PLACED | id=%s | rl_scale=%.3f | sentiment_adj=%+.2f | paper=%s',
                trade_id[:8], rl_scale, sentiment_adj, PAPER_MODE)
    # Create and save trade plan immediately on fill (guard against duplicates)
    try:
        _existing = [p for p in load_open_plans() if p.occ_symbol == occ_sym]
        if _existing:
            logger.info('[PLAN] Skipping duplicate plan for %s (existing: %s)', occ_sym, _existing[0].plan_id[:8])
        else:
            _plan_entry_px = limit_px if not PAPER_MODE else sig.entry_price
            _plan = create_plan(
            sig=sig, entry_price=_plan_entry_px, contracts=contracts,
            news_score=news_score, insider_score=insider_score,
            ca_score=ca_score, polymarket_score=polymarket_score,
            rl_kelly_scale=rl_scale, trade_id=trade_id,
            occ_symbol=occ_sym,
        )
        save_plan(_plan)
        logger.info('[PLAN] Created: %s | target=$%.2f stop=$%.2f deadline=%s R/R=%.1f | %s',
                   _plan.plan_id[:8], _plan.target_price, _plan.stop_price,
                   _plan.target_date[:10], _plan.risk_reward, _plan.entry_thesis)
    except Exception as _plan_err:
        logger.warning('[PLAN] Plan creation failed: %s', _plan_err)
    # Record signal context only after confirmed fill
    rl.record_signal_context(
        trade_id=trade_id, symbol=sig.symbol, strategy=sig.strategy,
        news_score=news_score, insider_score=insider_score, ca_score=ca_score,
        polymarket_score=polymarket_score,
        direction='call' if sig.kind == 'call' else 'put',
    )
    try:
        polymarket_scanner.track_trade_bet(
            trade_id=trade_id, symbol=sig.symbol,
            trade_direction='call' if sig.kind == 'call' else 'put',
            poly_score=polymarket_score,
        )
    except Exception:
        pass
    # Create and save trade plan
    # Telegram alert
    try:
        if _tg:
            _tg.alert_trade(
                symbol=sig.symbol, action=sig.action, strategy=sig.strategy,
                contracts=contracts, entry_price=limit_px,
                ev=sig.ev, kelly=sig.kelly_fraction * rl_scale,
                news_score=news_score, insider_score=insider_score,
            )
    except Exception as _tg_err:
        logger.debug('[TG] alert_trade failed: %s', _tg_err)
    return True, occ_sym, limit_px, contracts


# ── Market-hours cycle ─────────────────────────────────────────────────────────
def run_cycle():
    SEP = '=' * 70
    _acct   = get_account_state()
    capital = _acct['capital']
    opts_bp = _acct['options_buying_power']
    equity  = _acct['equity']

    # ── Refresh news intel during market hours (every 30 min) ──
    try:
        from options_v1 import news_scanner as _ns_mod
        _ni_path = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'news_intel.json'))
        _news_age = (time.time() - _ni_path.stat().st_mtime) if _ni_path.exists() else 9999
        if _news_age > 1800:   # older than 30 min
            logger.info('[NEWS] Refreshing news intel (age=%.0fm)...', _news_age / 60)
            _ns_mod.run_news_scan(watchlist.get_symbols(), force=True)
    except Exception as _ne:
        logger.debug('[NEWS] Intra-day refresh failed: %s', _ne)
    logger.info(SEP)
    logger.info('CYCLE START | capital=$%.2f | options_bp=$%.2f | equity=$%.2f | paper=%s | %s',
                capital, opts_bp, equity, PAPER_MODE, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))
    logger.info(SEP)

    # ── Intraday mark-to-market: feed open position P&L into RL ──
    try:
        open_pos = get_positions()
        for pos in open_pos:
            sym = pos.get('symbol', '')
            if len(sym) < 8:
                continue   # skip equities
            current_px  = float(pos.get('current_price', 0) or 0)
            upl         = float(pos.get('unrealized_pl', 0) or 0)
            upl_pct     = float(pos.get('unrealized_plpc', 0) or 0) * 100
            # Match to pending signal by underlying symbol
            underlying  = ''
            for i, c in enumerate(sym):
                if c.isdigit():
                    underlying = sym[:i]
                    break
            matched = False
            for tid, ctx in list(rl.weights.get('pending_signals', {}).items()):
                if ctx.get('symbol','') == underlying:
                    rl.mark_to_market(tid, current_px)
                    matched = True
                    logger.info('[RL] MTM %s: $%.2f → $%.2f | upl=$%.2f (%.1f%%)',
                                sym, ctx.get('entry_price', current_px), current_px, upl, upl_pct)
                    break
            if not matched and underlying:
                logger.debug('[RL] MTM: no pending signal found for %s', underlying)
    except Exception as e:
        logger.warning('[RL] Intraday MTM error: %s', e, exc_info=True)

    # ── Prune stale pending_signals older than 24h ────────────
    import time as _rltime
    _now = _rltime.time()
    _pending = rl.weights.setdefault('pending_signals', {})
    _stale   = [k for k, v in _pending.items() if _now - v.get('entry_ts', _now) > 86400]
    for k in _stale:
        del _pending[k]
    if _stale:
        logger.info('[RL] Pruned %d stale pending signals', len(_stale))
        rl._save_weights()

    # ── STEP 0: Monitor open trade plans (targets, stops, deadlines, OC) ──
    _open_plans = load_open_plans()
    _plans_freed = 0.0
    _plans_to_oc = []   # plans past deadline or deep loss → OC check later
    for _plan in _open_plans:
        try:
            _pq = get_market_quote(_plan.occ_symbol)
            if not _pq.get('ok'):
                logger.debug('[PLAN] No quote for %s', _plan.occ_symbol)
                continue
            _p_bid = _pq['bid']
            _p_mid = _pq['mid']
            _p_ask = _pq['ask']

            # Days to deadline
            from datetime import datetime as _dtx
            try:
                _plan_deadline = _dtx.fromisoformat(_plan.target_date.replace('Z','+00:00'))
                _days_to_dl = max(0, (_plan_deadline - _dtx.now(timezone.utc)).days)
            except Exception:
                _days_to_dl = 7

            # Check TARGET HIT
            if _p_bid >= _plan.target_price:
                logger.info('[PLAN] 🎯 TARGET HIT: %s | bid=%.2f >= target=%.2f',
                           _plan.symbol, _p_bid, _plan.target_price)
                _pnl = (_p_bid - _plan.entry_price) * 100 * _plan.contracts
                close_plan(_plan.plan_id, _p_bid, 'target_hit', _plan.contracts)
                rl.close_trade(_plan.trade_id, _p_bid, _pnl)
                try:
                    from datetime import datetime as _dtx2
                    _dh = max(1, (_dtx2.now() - _dtx2.fromisoformat(_plan.entry_ts.replace('Z','+00:00').replace('+00:00',''))).days)
                    rl.record_plan_outcome(_plan.plan_id, _plan.strategy, True, _plan.risk_reward, _dh, _plan.catalyst_window_days)
                except Exception: pass
                try:
                    close_position(_plan.occ_symbol)
                except Exception as _ce:
                    logger.warning('[PLAN] Close position failed: %s', _ce)
                _plans_freed += _p_bid * 100 * _plan.contracts
                try:
                    if _tg:
                        _tg._send(
                            f"🎯 TARGET HIT: {_plan.symbol}\n"
                            f"Entry: ${_plan.entry_price:.2f} → Exit: ${_p_bid:.2f}\n"
                            f"P&L: ${_pnl:.2f} | R/R: {_plan.risk_reward:.1f}:1"
                        )
                except Exception:
                    pass
                continue

            # Check STOP HIT
            if _p_bid <= _plan.stop_price:
                logger.info('[PLAN] 🛑 STOP HIT: %s | bid=%.2f <= stop=%.2f',
                           _plan.symbol, _p_bid, _plan.stop_price)
                _pnl = (_p_bid - _plan.entry_price) * 100 * _plan.contracts
                close_plan(_plan.plan_id, _p_bid, 'stop_hit', _plan.contracts)
                rl.close_trade(_plan.trade_id, _p_bid, _pnl)
                try:
                    from datetime import datetime as _dtx2
                    _dh = max(1, (_dtx2.now() - _dtx2.fromisoformat(_plan.entry_ts.replace('Z','+00:00').replace('+00:00',''))).days)
                    rl.record_plan_outcome(_plan.plan_id, _plan.strategy, False, -0.5, _dh, _plan.catalyst_window_days)
                except Exception: pass
                try:
                    close_position(_plan.occ_symbol)
                except Exception as _ce:
                    logger.warning('[PLAN] Close position failed: %s', _ce)
                _plans_freed += _p_bid * 100 * _plan.contracts
                try:
                    if _tg:
                        _tg._send(
                            f"🛑 STOP HIT: {_plan.symbol}\n"
                            f"Entry: ${_plan.entry_price:.2f} → Exit: ${_p_bid:.2f}\n"
                            f"P&L: ${_pnl:.2f} (stop triggered)"
                        )
                except Exception:
                    pass
                continue

            # Log monitoring status
            _loss_pct = (_plan.entry_price - _p_mid) / max(_plan.entry_price, 0.01) * 100
            logger.info(
                '[PLAN] MONITOR: %s | entry=$%.2f mid=$%.2f (%.1f%%) | '
                'target=$%.2f stop=$%.2f | deadline %dd',
                _plan.symbol, _plan.entry_price, _p_mid, -_loss_pct,
                _plan.target_price, _plan.stop_price, _days_to_dl
            )

            # Queue for OC check if past deadline or BP is low
            if _days_to_dl <= 0 or opts_bp < 50:
                _plans_to_oc.append((_plan, _pq, _days_to_dl))

        except Exception as _pe:
            logger.warning('[PLAN] Monitor error %s: %s', _plan.symbol, _pe)

    # Update opts_bp with freed capital from closed plans
    opts_bp += _plans_freed
    if _plans_freed > 0:
        logger.info('[PLAN] Freed $%.2f from closed plans → opts_bp=$%.2f', _plans_freed, opts_bp)

    # ── STEP 0: Monitor open trade plans (targets, stops, deadlines) ──────────
    _oc_engine = OpportunityCostEngine()
    _open_plans = load_open_plans()
    _plans_freed_capital = 0.0
    for _plan in _open_plans:
        try:
            _quote = get_market_quote(_plan.occ_symbol)
            _current_bid = _quote.get('bid', 0)
            _current_px = _quote.get('mid', 0)
            if not _quote.get('ok') or not _current_px:
                continue
            _days_to_deadline = max(0, (datetime.fromisoformat(_plan.target_date) - datetime.now(timezone.utc).replace(tzinfo=None)).days)
            # Check target hit
            if _current_bid >= _plan.target_price:
                logger.info('[PLAN] TARGET HIT: %s | bid=%.2f >= target=%.2f',
                           _plan.symbol, _current_bid, _plan.target_price)
                close_plan(_plan.plan_id, _current_bid, 'target_hit')
                rl.close_trade(_plan.trade_id, _current_bid,
                              (_current_bid - _plan.entry_price) * 100 * _plan.contracts)
                try:
                    if _tg: _tg._send(f"TARGET HIT: {_plan.symbol} | bid={_current_bid:.2f} >= target={_plan.target_price:.2f}")
                except Exception:
                    pass
                _plans_freed_capital += _current_bid * 100
                continue
            # Check stop hit
            if _current_bid <= _plan.stop_price:
                logger.info('[PLAN] STOP HIT: %s | bid=%.2f <= stop=%.2f',
                           _plan.symbol, _current_bid, _plan.stop_price)
                close_plan(_plan.plan_id, _current_bid, 'stop_hit')
                rl.close_trade(_plan.trade_id, _current_bid,
                              (_current_bid - _plan.entry_price) * 100 * _plan.contracts)
                _plans_freed_capital += _current_bid * 100
                continue
            logger.info('[PLAN] MONITOR: %s | entry=%.2f current=%.2f target=%.2f stop=%.2f | deadline=%s (%dd)',
                       _plan.symbol, _plan.entry_price, _current_bid,
                       _plan.target_price, _plan.stop_price, _plan.target_date[:10], _days_to_deadline)
        except Exception as _pe:
            logger.warning('[PLAN] Monitor error for %s: %s', _plan.symbol, _pe)

    # ── Order monitoring: cancel stale limit orders (>30 min unfilled) ──
    try:
        import time as _tm
        _open_orders = get_open_orders()
        for _ord in _open_orders:
            _oid = _ord.get('id', '')
            _created = _ord.get('created_at', '')
            _sym = _ord.get('symbol', '')
            if not _oid or not _created:
                continue
            try:
                from datetime import datetime as _dto, timezone as _dtz
                _age = (_dto.now(_dtz.utc) - _dto.fromisoformat(_created.replace('Z','+00:00'))).total_seconds()
                if _age > 1800:  # 30 min
                    logger.warning('[ORDER] Stale order %s for %s (%.0f min) — cancelling', _oid[:8], _sym, _age/60)
                    cancel_order(_oid)
                    try:
                        for _sp in [p for p in load_open_plans() if p.occ_symbol == _sym and p.status == 'open']:
                            logger.info('[ORDER] Expiring unfilled plan %s', _sp.plan_id[:8])
                            close_plan(_sp.plan_id, 0.0, 'order_expired', _sp.contracts)
                            try: rl.close_trade(_sp.trade_id, 0.0, -_sp.entry_price * 100 * _sp.contracts)
                            except Exception: pass
                    except Exception: pass
            except Exception as _oe:
                logger.debug('[ORDER] parse created_at failed: %s', _oe)
    except Exception as _oe2:
        logger.debug('[ORDER] order monitoring error: %s', _oe2)


    # ── Prune stale RL pending signals ─────────────────────────────────────
    try:
        _pruned = rl.prune_stale_pending(max_age_days=7)
        if _pruned:
            logger.info('[RL] Pruned %d stale pending signals', _pruned)
    except Exception:
        pass

    # Dynamic watchlist: expire TTLs + sync Alpaca every cycle; screener every 10 min
    try:
        raw_symbols = run_dynamic_update(watchlist)
        if not raw_symbols:
            raw_symbols = watchlist.get_symbols()
    except Exception as _dwl_err:
        logger.debug('[DWL] cycle update skipped: %s', _dwl_err)
        raw_symbols = watchlist.get_symbols()

    # Load signal intelligence for this cycle
    _news_scores      = {}
    _insider_scores   = {}
    _poly_scores      = {}
    _poly_macro_score = 0.0
    try:
        import json as _j
        from pathlib import Path as _P
        _ni = _P(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'news_intel.json'))
        if _ni.exists():
            _news_scores = {s: d.get('combined_score', d.get('news', {}).get('score', 0.0))
                            for s, d in _j.loads(_ni.read_text()).get('symbols', {}).items()}
    except Exception:
        pass
    try:
        import json as _j2
        from pathlib import Path as _P2
        _ii = _P2(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'insider_intel.json'))
        if _ii.exists():
            _insider_scores = {s: d.get('score', 0.0)
                               for s, d in _j2.loads(_ii.read_text()).get('data', {}).items()}
    except Exception:
        pass
    try:
        _poly_macro_score = polymarket_scanner.get_macro_score()
        for _sym in watchlist.get_symbols():
            _poly_scores[_sym] = polymarket_scanner.get_symbol_score(_sym)
        if any(abs(v) > 0.05 for v in _poly_scores.values()):
            logger.info('[STEP] Poly macro=%.3f | top: %s',
                        _poly_macro_score,
                        str({s:round(v,3) for s,v in _poly_scores.items() if abs(v)>0.05})[:120])
    except Exception:
        pass
    logger.info('[STEP 1] Watchlist: %d symbols -> %s', len(raw_symbols), raw_symbols)

    optionable = filter_optionable(raw_symbols)
    logger.info('[STEP 2] Options filter: %d/%d passed', len(optionable), len(raw_symbols))

    safe_symbols, risky, sentiment_map = filter_corporate_action_risks(optionable, days_ahead=14)
    if risky:
        for sym, acts in risky.items():
            ca_types = [a.get('ca_type','?') for a in acts]
            sent = sentiment_map.get(sym, {})
            logger.warning('[STEP 3] CA BLOCK: %s | events=%s | FinBERT=%s score=%.2f', sym, ca_types, sent.get('label','?'), sent.get('score',0))
    if sentiment_map:
        for sym, sent in sentiment_map.items():
            if sym in safe_symbols:
                logger.info('[STEP 3] CA safe: %s | FinBERT=%s score=%.2f conf=%.2f', sym, sent.get('label','?'), sent.get('score',0), sent.get('confidence',0))
    logger.info('[STEP 3] CA filter: %d safe / %d blocked', len(safe_symbols), len(risky))

    signals_input = get_finviz_signals(safe_symbols)
    logger.info('[STEP 5] Finviz signals: %d candidates', len(signals_input))
    for sym, d, sc, reason in signals_input:
        logger.info('[STEP 5]   %s | dir=%s score=%.0f | %s', sym, d, sc, str(reason)[:80])
    # Override direction using live news + poly scores when signal_bus unavailable
    _enhanced = []
    for _s, _d, _sc, _r in signals_input:
        _ns = _news_scores.get(_s, 0.0)
        _ps = _poly_scores.get(_s, 0.0)
        _combined = _ns * 0.6 + _ps * 0.4
        if abs(_combined) > 0.08:  # strong directional signal overrides default
            _d = 'call' if _combined > 0 else 'put'
            _sc = min(90, _sc + int(abs(_combined) * 20))
            logger.info('[STEP 5] Direction override %s: %s (news=%.2f poly=%.2f combined=%.2f)',
                       _s, _d.upper(), _ns, _ps, _combined)
        _enhanced.append((_s, _d, _sc, _r))
    signals_input = _enhanced

    call_syms = list(dict.fromkeys(s for s, d, sc, _ in signals_input if d == 'call' and sc >= 60))
    put_syms  = list(dict.fromkeys(s for s, d, sc, _ in signals_input if d == 'put'  and sc >= 60))
    if not call_syms and not put_syms:
        logger.info('[STEP 5] No scored signals — using full safe watchlist as calls')
        call_syms = safe_symbols
    logger.info('[STEP 5] Scanning: calls=%s | puts=%s', call_syms, put_syms)

    logger.info('[STEP 6] Running DCVX strategy scan...')
    dcvx_calls = dcvx_strat.scan(call_syms, capital, direction='call') if call_syms else []
    dcvx_puts  = dcvx_strat.scan(put_syms,  capital, direction='put')  if put_syms  else []
    all_signals = dcvx_calls + dcvx_puts
    approved = [s for s in all_signals if s.action != 'skip']
    logger.info('[STEP 6] %d signals | %d approved | %d skipped', len(all_signals), len(approved), len(all_signals)-len(approved))
    if opts_bp < 500 and approved:
        approved = sorted(approved, key=lambda s: s.entry_price * 100)
        logger.info('[STEP 6b] Low BP ($%.0f) — prioritizing cheapest contracts', opts_bp)
    else:
        approved = sorted(approved, key=lambda s: s.ev, reverse=True)

    # ── OC check: should we exit a loser to fund a better signal? ──
    if _plans_to_oc and approved:
        for (_oc_plan, _oc_quote, _oc_dte) in _plans_to_oc:
            try:
                _oc_mid = _oc_quote['mid']
                _oc_bid = _oc_quote['bid']
                # Get delta from snapshot
                _oc_delta = 0.15   # default conservative
                try:
                    _oc_snap = get_option_snapshot(_oc_plan.occ_symbol)
                    _oc_greeks = _oc_snap.get(_oc_plan.occ_symbol, {}).get('greeks', {})
                    _oc_delta = abs(float(_oc_greeks.get('delta', 0.15)))
                except Exception:
                    pass

                # Parse expiry DTE from OCC symbol (rough estimate)
                _oc_dte_actual = max(1, _oc_dte + _oc_plan.catalyst_window_days)

                _oc_freed = _oc_bid * 100 * _oc_plan.contracts
                _oc_result = oc_engine.evaluate(
                    plan=_oc_plan,
                    current_option_price=_oc_mid,
                    current_delta=_oc_delta,
                    days_remaining=_oc_dte_actual,
                    new_signals=approved,
                    freed_capital=_oc_freed,
                )

                _oc_plan.oc_checks = (_oc_plan.oc_checks or 0) + 1
                update_plan_oc(
                    _oc_plan.plan_id,
                    oc_checks=_oc_plan.oc_checks,
                    oc_switch_offered=_oc_result['should_switch'],
                    oc_ev_hold=_oc_result['ev_hold'],
                    oc_ev_new=_oc_result['ev_new'],
                )

                logger.info('[OC] %s: %s', _oc_plan.symbol, _oc_result['reason'])

                if _oc_result['should_exit']:
                    _oc_reason = _oc_result.get('force_reason') or 'oc_switch'
                    logger.warning('[OC] CLOSING %s | reason=%s', _oc_plan.symbol, _oc_reason)
                    try:
                        close_position(_oc_plan.occ_symbol)
                    except Exception as _ce:
                        _ce_str = str(_ce)
                        if '404' in _ce_str or 'Not Found' in _ce_str:
                            # Position never filled or already gone — cancel open order if any
                            logger.warning('[OC] Position %s not found (404) — cancelling pending orders and expiring plan', _oc_plan.occ_symbol)
                            try:
                                for _stale in get_open_orders():
                                    if _stale.get('symbol') == _oc_plan.occ_symbol:
                                        cancel_order(_stale['id'])
                                        logger.info('[OC] Cancelled pending order %s', _stale['id'][:8])
                            except Exception: pass
                            close_plan(_oc_plan.plan_id, 0.0, 'order_expired', _oc_plan.contracts)
                            try:
                                rl.close_trade(_oc_plan.trade_id, 0.0, -_oc_plan.entry_price * 100 * _oc_plan.contracts)
                            except Exception: pass
                        else:
                            logger.error('[OC] Close failed: %s', _ce)
                        continue
                    _oc_pnl = (_oc_bid - _oc_plan.entry_price) * 100 * _oc_plan.contracts
                    if not verify_position_closed(_oc_plan.occ_symbol, retries=3, delay=2.0):
                        logger.error('[OC] Position %s not confirmed closed — aborting plan close', _oc_plan.occ_symbol)
                        continue
                    close_plan(_oc_plan.plan_id, _oc_bid, _oc_reason, _oc_plan.contracts)
                    rl.close_trade(_oc_plan.trade_id, _oc_bid, _oc_pnl)
                    try:
                        from datetime import datetime as _dtx2
                        _dh = max(1, (_dtx2.now() - _dtx2.fromisoformat(_oc_plan.entry_ts.replace('Z','+00:00').replace('+00:00',''))).days)
                        rl.record_plan_outcome(_oc_plan.plan_id, _oc_plan.strategy, False, _oc_pnl / max(abs(_oc_plan.max_loss_dollars), 1), _dh, _oc_plan.catalyst_window_days)
                    except Exception: pass
                    opts_bp += _oc_freed
                    logger.info('[OC] Closed %s | freed $%.2f | pnl=$%.2f', _oc_plan.symbol, _oc_freed, _oc_pnl)

                    # Telegram alert
                    try:
                        if _tg:
                            _best = _oc_result.get('best_signal')
                            _alt = f"{_best.symbol} {_best.kind.upper()}" if _best else 'none'
                            _tg._send(
                                f"⚡ OC SWITCH: {_oc_plan.symbol}\n"
                                f"Closed at ${_oc_bid:.2f} (pnl ${_oc_pnl:.2f})\n"
                                f"EV(hold)=${_oc_result['ev_hold']:.0f} vs EV(new)=${_oc_result['ev_new']:.0f}\n"
                                f"Best alt: {_alt} ({_oc_result['switch_score']:.1f}×)"
                            )
                    except Exception:
                        pass
            except Exception as _oe:
                logger.warning('[OC] Error for %s: %s', _oc_plan.symbol, _oe)

    # ── OC check: should we exit a loser to fund a better signal? ──────────
    _open_plans_oc = load_open_plans()  # reload after STEP 0 closures
    _any_past_deadline = any(
        max(0, (datetime.fromisoformat(p.target_date) - datetime.now(timezone.utc).replace(tzinfo=None)).days) <= 0
        for p in _open_plans_oc
    )
    if _open_plans_oc and (opts_bp < 50 or _any_past_deadline):
        for _plan_oc in _open_plans_oc:
            if _plan_oc.status != 'open':
                continue
            try:
                _quote_oc = get_market_quote(_plan_oc.occ_symbol)
                if not _quote_oc.get('ok'):
                    continue
                _delta_oc = 0.15  # conservative default (no greeks endpoint available)
                _days_rem_oc = max(1, (datetime.fromisoformat(_plan_oc.target_date) - datetime.now(timezone.utc).replace(tzinfo=None)).days)
                _freed_cap_oc = _quote_oc.get('bid', 0) * 100 * _plan_oc.contracts
                _oc = _oc_engine.evaluate(
                    plan=_plan_oc,
                    current_option_price=_quote_oc.get('mid', 0),
                    current_delta=_delta_oc,
                    days_remaining=_days_rem_oc,
                    new_signals=approved,
                    freed_capital=_freed_cap_oc,
                )
                _plan_oc.oc_checks += 1
                if _oc.get('should_exit'):
                    logger.warning('[OC] %s: %s', _plan_oc.symbol, _oc['reason'])
                    if _tg:
                        try:
                            _tg._send(
                                f"OC ALERT: {_plan_oc.symbol}\n"
                                f"{_oc['reason']}\n"
                                f"Auto-closing in next cycle unless manually held."
                            )
                        except Exception:
                            pass
                    if _oc.get('force_reason') or _oc.get('should_switch'):
                        try:
                            close_position(_plan_oc.occ_symbol)
                        except Exception as _cp_err:
                            logger.warning('[OC] close_position failed: %s', _cp_err)
                        close_plan(_plan_oc.plan_id, _quote_oc.get('bid', 0),
                                   _oc.get('force_reason') or 'oc_switch')
                        rl.close_trade(_plan_oc.trade_id, _quote_oc.get('bid', 0),
                                       (_quote_oc.get('bid', 0) - _plan_oc.entry_price) * 100 * _plan_oc.contracts)
                        _plans_freed_capital += _freed_cap_oc
                        logger.info('[OC] Closed %s | freed $%.2f', _plan_oc.symbol, _plans_freed_capital)
            except Exception as _oc_err:
                logger.warning('[OC] Error for %s: %s', _plan_oc.symbol, _oc_err)
    opts_bp += _plans_freed_capital

    logger.info('[STEP 7] Executing %d approved signals...', len(approved))

    # ── Dealer-flow gate: scan GEX/VEX/CEX for all signal symbols ─────────────
    _cycle_gex: dict = {}
    try:
        _gex_scanner = get_gamma_scanner()
        _gex_spot_map = {}
        for _gs in all_signals:
            _sp = get_spot(_gs.symbol)
            if _sp:
                _gex_spot_map[_gs.symbol] = _sp
        if _gex_spot_map:
            _cycle_gex = _gex_scanner.scan_watchlist(list(_gex_spot_map.keys()), _gex_spot_map)
            logger.info('[GEX] Cycle dealer-flow: %d symbols scanned',
                        len(_cycle_gex))
    except Exception as _gex_cycle_err:
        logger.warning('[GEX] Cycle scan failed: %s', _gex_cycle_err)

    trades_placed = 0
    for sig in all_signals:
        _sym_poly     = _poly_scores.get(sig.symbol, 0.0)
        _blended_poly = round(_sym_poly * 0.6 + _poly_macro_score * 0.4, 4)

        # ── Dealer-pressure gate ───────────────────────────────────────────────
        # Only fire calls when dealer pressure is net positive (dealers buying)
        # Only fire puts when dealer pressure is net negative (dealers selling)
        # Neutral/missing → allow (no data = don't block)
        _gex_prof = _cycle_gex.get(sig.symbol)
        if _gex_prof is not None:
            _dp = _gex_prof.dealer_pressure   # signed, billion-scale
            _direction = getattr(sig, 'direction', 'call')
            if _direction == 'call' and _dp < -5.0:
                logger.info('[GEX-GATE] BLOCKED %s CALL — dealer_pressure=%.1fB (selling bias)',
                            sig.symbol, _dp)
                continue
            if _direction == 'put' and _dp > 5.0:
                logger.info('[GEX-GATE] BLOCKED %s PUT — dealer_pressure=%.1fB (buying bias)',
                            sig.symbol, _dp)
                continue
            logger.debug('[GEX-GATE] %s %s passed — dealer_pressure=%.1fB',
                         sig.symbol, _direction, _dp)

        _exec_result = execute_signal(
            sig, capital,
            ca_sentiment=sentiment_map.get(sig.symbol),
            news_score=_news_scores.get(sig.symbol, 0.0),
            insider_score=_insider_scores.get(sig.symbol, 0.0),
            polymarket_score=_blended_poly,
            options_bp=opts_bp,
        )
        placed = bool(_exec_result)
        if placed:
            # Submit GTC take-profit order to Alpaca immediately
            try:
                if not PAPER_MODE and isinstance(_exec_result, tuple):
                    _, _gtc_sym, _entry_px, _gtc_qty = _exec_result
                    # Load the plan just created to get target price
                    _new_plans = load_open_plans()
                    _new_plan  = next((p for p in _new_plans if p.occ_symbol == _gtc_sym), None)
                    if _new_plan:
                        _gtc = submit_gtc_exit_order(_gtc_sym, _gtc_qty, _new_plan.target_price)
                        if _gtc.get('id'):
                            logger.info('[GTC] Take-profit order submitted: %s @ $%.2f | id=%s',
                                       _gtc_sym, _new_plan.target_price, _gtc['id'][:8])
            except Exception as _gtc_err:
                logger.warning('[GTC] Take-profit setup failed: %s', _gtc_err)
            logger.info('[SIGNAL] %s -> placed | news=%.3f insider=%.3f poly=%.3f',
                        sig.symbol,
                        _news_scores.get(sig.symbol, 0.0),
                        _insider_scores.get(sig.symbol, 0.0),
                        _blended_poly)
            trades_placed += 1
    logger.info('[STEP 7] Trades placed: %d', trades_placed)

    portfolio_risk = risk_manager.evaluate(spot=0)
    logger.info('[STEP 8] Greeks | delta=%.2f gamma=%.5f vega=%.2f theta=%.5f | within_limits=%s',
                portfolio_risk.net_delta, portfolio_risk.net_gamma,
                portfolio_risk.net_vega, portfolio_risk.net_theta,
                portfolio_risk.within_limits)
    if not portfolio_risk.within_limits:
        logger.warning('[STEP 8] RISK BREACH: %s', portfolio_risk.breach_reasons)

    rl_s = rl.summary()
    logger.info('[STEP 9] RL | DCVX: n_trades=%s win_rate=%s kelly_scale=%s total_pnl=$%s',
                rl_s['n_trades'].get('DCVX',0),
                '{:.1%}'.format(float(rl_s['win_rates'].get('DCVX',0.5))),
                '{:.3f}'.format(float(rl_s['kelly_scales'].get('DCVX',1.0))),
                '{:.2f}'.format(float(rl_s['total_pnl'].get('DCVX',0))))
    logger.info(SEP)
    # Write health check file
    try:
        import json as _hj
        _health = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'cycle_ok': True,
            'capital': capital,
            'options_bp': opts_bp,
            'trades_placed': trades_placed,
            'open_plans': len(load_open_plans()),
            'consecutive_errors': globals().get('_consecutive_errors', 0),
        }
        _htmp = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'health.json.tmp'))
        _htmp.write_text(_hj.dumps(_health))
        _htmp.rename(Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'health.json')))
    except Exception as _he:
        logger.debug('[HEALTH] write failed: %s', _he)
    logger.info('CYCLE COMPLETE | trades=%d | capital=$%.2f', trades_placed, capital)
    logger.info(SEP)


# ── Overnight prep cycle (every 20 min) ───────────────────────────────────────
_last_rl_review = 0.0
_last_ca_scan        = 0.0
_last_insider_scan   = 0.0
_last_poly_scan      = 0.0

def run_overnight_prep():
    """
    Runs every 10 min while market is closed.
    Does everything possible to prepare for market open:
    1. RL mark-to-market review (every 2h)
    2. News scan + FinBERT sentiment on all symbols (every cycle)
    3. Pre-market prices, gap detection, VIX regime
    4. Refresh option chain availability cache
    5. Synthetic pricing — reprice all contracts with live spot
    6. FinBERT pre-score upcoming corporate actions
    7. Opening estimates when <15 min to open
    """
    global _last_rl_review, _last_ca_scan
    logger.info('=== OVERNIGHT PREP (10-min cycle) ===')
    now = time.time()

    # 1. RL mark-to-market (every 2h)
    if now - _last_rl_review >= OVERNIGHT_LEARN_SECS:
        reviewed = rl.overnight_review(get_spot)
        logger.info('RL overnight review: %d positions | %s', reviewed, rl.summary())
        _last_rl_review = now
    else:
        logger.info('RL review in %.0f min', (OVERNIGHT_LEARN_SECS - (now - _last_rl_review)) / 60)

    # 2. Pull watchlist + run dynamic symbol discovery (6h cooldown)
    symbols = watchlist.get_symbols()
    try:
        news_cache_pre = {}  # populated after news scan below; used for next DWL run
        updated_symbols = run_dynamic_update(watchlist, news_cache=None)
        if set(updated_symbols) != set(symbols):
            added   = [s for s in updated_symbols if s not in symbols]
            removed = [s for s in symbols if s not in updated_symbols]
            logger.info('[DWL] Watchlist updated: +%s -%s → %d total', added, removed, len(updated_symbols))
            symbols = updated_symbols
        else:
            logger.info('[DWL] Watchlist unchanged: %d symbols', len(symbols))
        dwl_status = get_dynamic_status()
        if dwl_status['dynamic_symbols']:
            logger.info('[DWL] Dynamic additions: %s', list(dwl_status['dynamic_symbols'].keys()))
    except Exception as _dwl_e:
        logger.warning('[DWL] Dynamic update failed (using existing watchlist): %s', _dwl_e)

    # 3. News scan + pre-market prices + VIX (every cycle — fresh intel)
    try:
        intel = run_news_scan(symbols)
        vix   = intel.get('vix', {})
        spy_gap = intel.get('market_proxy')
        logger.info('Market intel | VIX=%.1f (%s) | SPY gap=%s%%',
                    vix.get('level') or 0,
                    vix.get('regime', '?'),
                    spy_gap or 'n/a')

        # Log any big movers
        for sym, data in intel.get('symbols', {}).items():
            gap = data.get('gap_pct') or 0
            if abs(gap) >= 2.0:
                direction = '▲' if gap > 0 else '▼'
                logger.info('OVERNIGHT MOVER: %s %s%.1f%% | news=%s | signal=%s',
                            sym, direction, abs(gap),
                            data.get('news', {}).get('label', '?'),
                            data.get('direction', '?'))
    except Exception as e:
        logger.warning('News scan failed: %s', e)

    # 3b. GEX scan — dealer gamma exposure across all symbols
    try:
        _gamma_scanner = get_gamma_scanner()
        spot_map = {}
        for _s in symbols:
            _sp = get_spot(_s)
            if _sp: spot_map[_s] = _sp
        gex_results = _gamma_scanner.scan_watchlist(symbols, spot_map)
        squeeze_syms = [s for s, p in gex_results.items() if p.squeeze_active]
        total_spy_gex = gex_results.get('SPY', None)
        if total_spy_gex:
            logger.info('[GEX] SPY: %s', total_spy_gex.summary())
        if squeeze_syms:
            logger.info('[GEX] 🔴 SQUEEZE DETECTED: %s', squeeze_syms)
        else:
            logger.info('[GEX] Scanned %d symbols — no squeeze active', len(gex_results))
    except Exception as _gex_e:
        logger.warning('[GEX] scan failed: %s', _gex_e)

    # 4. Refresh option chain availability cache
    refreshed = 0
    for sym in symbols:
        try:
            _options_cache[sym] = len(get_option_chain(sym)) > 0
            refreshed += 1
        except Exception as e:
            logger.debug('Chain refresh failed for %s: %s', sym, e)
    optionable = [s for s in symbols if _options_cache.get(s)]
    logger.info('Chain cache: %d/%d optionable via Alpaca', len(optionable), len(symbols))

    # 5. Synthetic pricing (all symbols — yfinance works 24/7)
    try:
        synthetic.refresh_iv_surface(symbols)
        synth_prices = synthetic.price_watchlist(symbols)
        total = sum(len(v) for v in synth_prices.values())
        logger.info('Synthetic reprice: %d contracts across %d symbols', total, len(synth_prices))
    except Exception as e:
        logger.warning('Synthetic pricing failed: %s', e)

    # 6. FinBERT pre-score corporate actions (every 2h — CA data rarely changes)
    # Insider activity scan (every 4h overnight)
    global _last_insider_scan
    if now - _last_insider_scan >= 14400:
        try:
            logger.info('[OVERNIGHT] Insider activity scan...')
            ins_data = insider_scanner.run_insider_scan(symbols)
            bull_ins = [s for s,d in ins_data.items() if d.get('label') == 'bullish']
            bear_ins = [s for s,d in ins_data.items() if d.get('label') == 'bearish']
            if bull_ins:
                logger.info('[INSIDER] Bullish insiders: %s', bull_ins)
            if bear_ins:
                logger.info('[INSIDER] Bearish insiders: %s', bear_ins)
            _last_insider_scan = now
        except Exception as e:
            logger.error('[OVERNIGHT] Insider scan error: %s', e)

    # Polymarket scan (every 30 min)
    global _last_poly_scan
    if now - _last_poly_scan >= 1800:
        try:
            logger.info('[OVERNIGHT] Polymarket scan...')
            poly_intel = polymarket_scanner.run_polymarket_scan()
            relevant   = poly_intel.get('relevant', 0)
            macro_n    = len(poly_intel.get('macro', []))
            hc_n       = len(poly_intel.get('high_conviction', []))
            logger.info('[POLY] %d relevant markets | %d macro | %d high-conviction',
                        relevant, macro_n, hc_n)
            # Log top macro signals
            for sig in poly_intel.get('macro', [])[:5]:
                logger.info('[POLY] %s | YES=%.0f%% | %s',
                            sig['macro'], sig['yes_prob']*100, sig['question'][:70])
            # Log symbol-specific signals
            for sym, sigs in poly_intel.get('by_symbol', {}).items():
                if sigs:
                    top = sigs[0]
                    logger.info('[POLY] %s: %s YES=%.0f%% %s',
                                sym, top['direction'].upper(), top['yes_prob']*100,
                                top['question'][:60])
            # Fire Telegram alerts
            polymarket_scanner.send_alerts(poly_intel)
            _last_poly_scan = now
        except Exception as e:
            logger.error('[OVERNIGHT] Polymarket scan error: %s', e)

    if now - _last_ca_scan >= OVERNIGHT_LEARN_SECS:
        try:
            from options_v1.calendar import get_upcoming_corporate_actions
            from options_v1.finbert_sentiment import get_symbol_sentiment
            ca_map = get_upcoming_corporate_actions(symbols, days_ahead=14)
            if ca_map:
                for sym, actions in ca_map.items():
                    get_symbol_sentiment(sym, actions)
                logger.info('FinBERT pre-scored CAs: %s', list(ca_map.keys()))
            else:
                logger.info('No upcoming corporate actions')
            _last_ca_scan = now
        except Exception as e:
            logger.warning('CA/FinBERT prep failed: %s', e)

    # 7. Next open countdown + final estimates when <15 min away
    try:
        # Telegram VIX alert if elevated
        try:
            import json as _jv
            from pathlib import Path as _Pv
            _ni = _Pv(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))) / 'news_intel.json'))
            if _tg and _ni.exists():
                _vd = _jv.loads(_ni.read_text()).get('vix', {})
                _tg.alert_vix(_vd.get('level', 0), _vd.get('regime', 'unknown'))
        except Exception:
            pass

        from options_v1.calendar import get_next_open
        next_open = get_next_open()
        if next_open:
            secs = (next_open - datetime.now(timezone.utc)).total_seconds()
            logger.info('Market open in %.1f min (%s ET)', secs / 60, next_open.strftime('%H:%M'))
            if 0 < secs < 900:
                logger.info('FINAL PREP: running opening estimates (<15 min to bell)')
                synthetic.get_opening_estimates(symbols)
    except Exception:
        pass

    logger.info('=== PREP COMPLETE ===')


# ── Main loop ──────────────────────────────────────────────────────────────────
def reconcile_positions():
    """Verify open trade plans match actual Alpaca positions on startup."""
    logger.info('[RECONCILE] Checking open plans vs live positions...')
    try:
        plans = load_open_plans()
        if not plans:
            logger.info('[RECONCILE] No open plans')
            return
        live = set()
        for p in (get_positions() or []):
            sym = p.get('symbol', '')
            if len(sym) > 6:
                live.add(sym)
        logger.info('[RECONCILE] Live positions: %s', live or 'none')
        for plan in plans:
            if plan.occ_symbol not in live:
                logger.warning('[RECONCILE] %s NOT found — cancelling orders and marking expired', plan.occ_symbol)
                # Cancel any pending orders for this symbol
                try:
                    for _ro in get_open_orders():
                        if _ro.get('symbol') == plan.occ_symbol:
                            cancel_order(_ro['id'])
                            logger.info('[RECONCILE] Cancelled pending order %s for %s', _ro['id'][:8], plan.occ_symbol)
                except Exception: pass
                close_plan(plan.plan_id, 0.0, 'reconcile_expired', plan.contracts)
                try:
                    rl.close_trade(plan.trade_id, 0.0, -plan.entry_price * 100 * plan.contracts)
                except Exception:
                    pass
            else:
                logger.info('[RECONCILE] %s confirmed live OK', plan.occ_symbol)
    except Exception as e:
        logger.warning('[RECONCILE] %s', e)


def main():
    global _consecutive_errors
    logger.info('Options V1 starting. PAPER_MODE=%s', PAPER_MODE)
    reconcile_positions()

    while not _shutdown_event.is_set():
        try:
            if is_market_open():
                run_cycle()
                _consecutive_errors = 0
                _shutdown_event.wait(timeout=CYCLE_SECS)
            else:
                run_overnight_prep()
                _consecutive_errors = 0
                _shutdown_event.wait(timeout=OVERNIGHT_CYCLE_SECS)
        except Exception as e:
            _consecutive_errors += 1
            ce = _consecutive_errors
            logger.error('[CYCLE ERR %d] %s', ce, e, exc_info=True)
            if ce >= 5:
                bp = min(300, 30 * ce)
                logger.critical('[CIRCUIT BREAKER] %d consecutive errors — backoff %ds', ce, bp)
                # Use event wait so SIGTERM can interrupt the backoff immediately
                _shutdown_event.wait(timeout=bp)
            else:
                _shutdown_event.wait(timeout=60)
    logger.info('[SHUTDOWN] Options V1 bot exited cleanly')


if __name__ == '__main__':
    main()
