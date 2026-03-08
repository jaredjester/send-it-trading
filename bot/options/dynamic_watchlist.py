"""
Dynamic Watchlist Manager for Options Trading
========================================
Runs each overnight prep cycle to:
  1. Pull Alpaca most-actives + movers screener
  2. Filter for quality (price >$10, optionable, news sentiment positive)
  3. TTL-based additions — dynamic symbols live for 3 days, then expire
  4. Core symbols (SPY, QQQ, AAPL, etc.) are pinned and never pruned
  5. Caps watchlist at MAX_SYMBOLS total (cheapest API calls)

State file: DATA_DIR/dynamic_watchlist.json (DATA_DIR defaults to repo_root/data)
  {
    "dynamic": {
      "COIN": {"added_ts": 1234567890, "ttl_days": 3, "reason": "top_mover+news"}
    },
    "last_run": 1234567890
  }
"""
import os
import json
import time
import logging
import requests
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid circular deps
_tg = None
def _get_tg():
    global _tg
    if _tg is None:
        try:
            from options import telegram_alerts as _ta
            _tg = _ta
        except Exception:
            pass
    return _tg

# ── Config ────────────────────────────────────────────────────────────────────
CORE_SYMBOLS = [
    # Large-cap indices — always scan
    'SPY', 'QQQ',
    # Mega-cap with deep options chains
    'AAPL', 'MSFT', 'TSLA', 'NVDA', 'AMD', 'META', 'AMZN', 'GOOGL',
    # High-vol / high-options-liquidity tickers
    'COIN', 'NFLX', 'UBER', 'BABA', 'PLTR',
]
MAX_SYMBOLS   = 20          # hard cap on total watchlist size
MIN_PRICE     = 10.0        # ignore sub-$10 stocks
MAX_PRICE     = 2000.0      # ignore >$2k (options expensive, illiquid)
MIN_NEWS      = 0.05        # minimum positive news sentiment to add
TTL_DAYS      = 3           # how long a dynamic symbol stays
SCREENER_TOP  = 30          # how many candidates to pull from each screener
SCREENER_INTERVAL = 600     # re-run screener every 10 min; TTL+sync happens every call

STATE_FILE = Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data')))/'dynamic_watchlist.json'
ALPACA_DATA = 'https://data.alpaca.markets'
ALPACA_BASE = 'https://api.alpaca.markets'


def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
    }


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {'dynamic': {}, 'last_run': 0}


def _save_state(state: dict):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)


def _has_options(symbol: str) -> bool:
    """Quick check: does this symbol have tradeable options on Alpaca?"""
    try:
        r = requests.get(
            f'{ALPACA_BASE}/v2/options/contracts',
            headers=_headers(),
            params={'underlying_symbols': symbol, 'limit': 1, 'expiration_date_gte': '2026-01-01'},
            timeout=6,
        )
        if r.ok:
            return len(r.json().get('option_contracts', [])) > 0
    except Exception:
        pass
    return False


def _get_candidates() -> List[Dict]:
    """Pull most-actives + movers from Alpaca screener, return raw candidate list."""
    candidates = {}
    h = _headers()

    # 1. Most actives by dollar volume (better quality than share volume)
    try:
        r = requests.get(
            f'{ALPACA_DATA}/v1beta1/screener/stocks/most-actives',
            headers=h,
            params={'top': SCREENER_TOP, 'by': 'trades'},
            timeout=10,
        )
        if r.ok:
            for item in r.json().get('most_actives', []):
                sym = item.get('symbol', '')
                if sym:
                    candidates[sym] = candidates.get(sym, 0) + 1  # score: appeared in screener
                    logger.debug('[DWL] Candidate from most-actives: %s', sym)
    except Exception as e:
        logger.warning('[DWL] most-actives screener failed: %s', e)

    # 2. Top gainers (momentum plays)
    try:
        r = requests.get(
            f'{ALPACA_DATA}/v1beta1/screener/stocks/movers',
            headers=h,
            params={'top': SCREENER_TOP},
            timeout=10,
        )
        if r.ok:
            data = r.json()
            for item in data.get('gainers', []) + data.get('losers', []):
                sym = item.get('symbol', '')
                chg = abs(item.get('percent_change', 0))
                # Only care about big moves (>3%)
                if sym and chg > 3.0:
                    candidates[sym] = candidates.get(sym, 0) + 2  # movers weighted higher
                    logger.debug('[DWL] Candidate from movers: %s (chg=%.1f%%)', sym, chg)
    except Exception as e:
        logger.warning('[DWL] movers screener failed: %s', e)

    # Sort by score (appeared in multiple screeners = higher priority)
    return sorted(candidates.items(), key=lambda x: -x[1])


def _get_news_score(symbol: str) -> float:
    """Get news sentiment for a symbol from the news scanner cache."""
    try:
        from options.news_scanner import get_symbol_score
        score = get_symbol_score(symbol)
        return score if score is not None else 0.0
    except Exception:
        pass
    # Fallback: hit Alpaca news directly
    try:
        r = requests.get(
            f'{ALPACA_BASE}/v2/news',
            headers=_headers(),
            params={'symbols': symbol, 'limit': 5},
            timeout=6,
        )
        if r.ok:
            articles = r.json().get('news', [])
            return 0.1 if articles else 0.0  # at least some news = slightly positive
    except Exception:
        pass
    return 0.0


def _get_price(symbol: str) -> float:
    """Get latest trade price for a symbol."""
    try:
        r = requests.get(
            f'{ALPACA_DATA}/v2/stocks/{symbol}/trades/latest',
            headers=_headers(),
            timeout=6,
        )
        if r.ok:
            return float(r.json().get('trade', {}).get('p', 0))
    except Exception:
        pass
    return 0.0


def run_dynamic_update(watchlist_manager, news_cache: dict = None, force: bool = False) -> List[str]:
    """
    Main entry point. Call from overnight prep cycle.
    Returns the updated list of symbols for this cycle.
    
    Args:
        watchlist_manager: WatchlistManager instance (for add/sync)
        news_cache: dict of {symbol: score} from news_scanner (to avoid re-fetching)
        force: bypass cooldown check
    """
    state = _load_state()
    now = time.time()

    # ── Always: expire stale TTLs + sync Alpaca watchlist (cheap) ──────────
    expired_quick = []
    for sym, meta in list(state['dynamic'].items()):
        age_days = (now - meta.get('added_ts', 0)) / 86400
        if age_days > meta.get('ttl_days', TTL_DAYS):
            expired_quick.append(sym)
            del state['dynamic'][sym]
            logger.info('[DWL] Expired dynamic symbol: %s (%.1f days old)', sym, age_days)

    if expired_quick:
        _save_state(state)
        try:
            tg = _get_tg()
            if tg:
                tg.alert_watchlist_add([], expired_quick)
        except Exception:
            pass

    # Sync Alpaca watchlist every single cycle (1 PUT call)
    current_list = _build_symbol_list(state)
    try:
        watchlist_manager.sync(current_list)
    except Exception:
        pass

    # ── Throttled: run screener every SCREENER_INTERVAL ──────────────────
    if not force and (now - state.get('last_run', 0)) < SCREENER_INTERVAL:
        logger.debug('[DWL] Screener on cooldown (%.0f min ago) — %d symbols synced',
                     (now - state['last_run']) / 60, len(current_list))
        return current_list

    logger.info('[DWL] Running screener update...')
    expired = expired_quick  # already handled above

    # 2. Get candidates from screener
    candidates = _get_candidates()
    logger.info('[DWL] Got %d raw candidates from screener', len(candidates))

    # 3. Filter and score candidates
    current_syms = set(_build_symbol_list(state))
    added = []
    checked = 0

    for sym, screener_score in candidates:
        if len(current_syms) >= MAX_SYMBOLS:
            logger.info('[DWL] At max symbols (%d), stopping candidate evaluation', MAX_SYMBOLS)
            break

        # Skip already-tracked symbols
        if sym in current_syms or sym in CORE_SYMBOLS:
            continue

        # Skip obvious junk (warrant tickers, rights, units)
        if any(c in sym for c in ['+', '.', 'W', 'R', 'U']) and len(sym) > 5:
            continue

        checked += 1

        # Price filter
        price = _get_price(sym)
        if price < MIN_PRICE or price > MAX_PRICE:
            logger.debug('[DWL] Skipping %s — price $%.2f out of range', sym, price)
            continue

        # News filter
        news_score = (news_cache or {}).get(sym, _get_news_score(sym))
        if news_score < MIN_NEWS:
            logger.debug('[DWL] Skipping %s — news score %.3f too low', sym, news_score)
            continue

        # Options availability check (most expensive — do last)
        if not _has_options(sym):
            logger.debug('[DWL] Skipping %s — no options on Alpaca', sym)
            continue

        # Passed all filters — add it!
        reason = f'screener_score={screener_score}|news={news_score:.2f}|price=${price:.0f}'
        state['dynamic'][sym] = {
            'added_ts': now,
            'ttl_days': TTL_DAYS,
            'reason': reason,
            'price_at_add': price,
            'news_at_add': news_score,
        }
        current_syms.add(sym)
        added.append(sym)
        logger.info('[DWL] ✅ Added dynamic symbol: %s (%s)', sym, reason)

        # Add to Alpaca watchlist
        watchlist_manager.add(sym)

    # 4. Remove expired symbols from Alpaca watchlist
    for sym in expired:
        if sym not in CORE_SYMBOLS:
            watchlist_manager.remove(sym)
            logger.info('[DWL] Removed expired %s from Alpaca watchlist', sym)

    # 5. Save state
    state['last_run'] = now
    _save_state(state)

    # 6. Sync Alpaca watchlist with the final authoritative symbol list
    final_list = _build_symbol_list(state)
    try:
        watchlist_manager.sync(final_list)
        logger.info('[DWL] Synced Alpaca watchlist → %d symbols', len(final_list))
    except Exception as _sync_e:
        logger.warning('[DWL] Alpaca watchlist sync failed: %s', _sync_e)

    # 7. Log summary
    logger.info('[DWL] Update complete: %d checked, %d added, %d expired, %d total symbols',
                checked, len(added), len(expired), len(final_list))
    if added:
        logger.info('[DWL] New symbols: %s', added)
    if expired:
        logger.info('[DWL] Expired symbols: %s', expired)

    # Telegram alert for new additions or expirations
    if added or expired:
        try:
            tg = _get_tg()
            if tg:
                tg_added = []
                for sym in added:
                    meta = state['dynamic'].get(sym, {})
                    tg_added.append({
                        'symbol':         sym,
                        'reason':         meta.get('reason', ''),
                        'price':          meta.get('price_at_add', 0),
                        'news_score':     meta.get('news_at_add', 0),
                        'screener_score': meta.get('screener_score', 0),
                        'ttl_days':       meta.get('ttl_days', TTL_DAYS),
                    })
                tg.alert_watchlist_add(tg_added, expired if expired else [])
                logger.info('[DWL] Telegram alert sent: +%d added, %d expired', len(added), len(expired))
        except Exception as _tg_e:
            logger.warning('[DWL] Telegram alert failed: %s', _tg_e)

    return final_list


def _build_symbol_list(state: dict) -> List[str]:
    """Build the full symbol list: core + active dynamic symbols."""
    dynamic = [s for s in state.get('dynamic', {}).keys()]
    # Core first, then dynamic (deduped)
    result = list(CORE_SYMBOLS)
    for s in dynamic:
        if s not in result:
            result.append(s)
    return result[:MAX_SYMBOLS]


def get_dynamic_status() -> dict:
    """Return current dynamic watchlist state for dashboard/logging."""
    state = _load_state()
    now = time.time()
    dynamic_info = {}
    for sym, meta in state.get('dynamic', {}).items():
        age_days = (now - meta.get('added_ts', 0)) / 86400
        ttl = meta.get('ttl_days', TTL_DAYS)
        dynamic_info[sym] = {
            'age_days': round(age_days, 1),
            'ttl_days': ttl,
            'days_left': round(ttl - age_days, 1),
            'reason': meta.get('reason', ''),
        }
    return {
        'core_symbols': CORE_SYMBOLS,
        'dynamic_symbols': dynamic_info,
        'last_run': state.get('last_run', 0),
        'total': len(CORE_SYMBOLS) + len(dynamic_info),
    }
