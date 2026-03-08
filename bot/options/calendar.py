"""
Alpaca Calendar + Corporate Actions helpers for Options V1.
Now with FinBERT sentiment scoring on corporate action announcements.
"""

import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Optional
import time

_ca_filter_cache = {}
_CA_CACHE_TTL = 43200
logger = logging.getLogger(__name__)

ALPACA_BASE = 'https://api.alpaca.markets'

RISKY_CA_TYPES = {'forward_split', 'reverse_split', 'unit_split',
                  'merger', 'spinoff', 'cash_merger', 'stock_merger'}
WARN_CA_TYPES  = {'stock_dividend', 'special_dividend'}


def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
    }


# ── Calendar ──────────────────────────────────────────────────────────────────

def get_calendar(start: str, end: str) -> List[Dict]:
    try:
        r = requests.get(f'{ALPACA_BASE}/v2/calendar', headers=_headers(),
                         params={'start': start, 'end': end}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error('Calendar fetch failed: %s', e)
        return []


def get_next_open() -> Optional[datetime]:
    try:
        r = requests.get(f'{ALPACA_BASE}/v2/clock', headers=_headers(), timeout=5)
        clock = r.json()
        if clock.get('is_open'):
            return None
        next_open_str = clock.get('next_open')
        if next_open_str:
            return datetime.fromisoformat(next_open_str.replace('Z', '+00:00'))
    except Exception as e:
        logger.warning('get_next_open clock failed: %s — trying calendar', e)

    today = datetime.now(timezone.utc)
    end   = today + timedelta(days=7)
    days  = get_calendar(today.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    for day in days:
        open_str = day.get('open', '09:30')
        date_str = day.get('date')
        if not date_str:
            continue
        candidate = datetime.strptime(f"{date_str} {open_str}", '%Y-%m-%d %H:%M')
        candidate = candidate.replace(tzinfo=timezone.utc) + timedelta(hours=5)
        if candidate > today:
            return candidate
    return None


def sleep_until_open(buffer_secs: int = 300):
    next_open = get_next_open()
    if next_open is None:
        return
    now  = datetime.now(timezone.utc)
    wake = next_open - timedelta(seconds=buffer_secs)
    secs = (wake - now).total_seconds()
    if secs <= 0:
        return
    logger.info('Market closed. Next open: %s ET | Sleeping %.1fh (wake %s ET for warmup)',
                next_open.strftime('%Y-%m-%d %H:%M'), secs / 3600, wake.strftime('%H:%M'))
    time.sleep(max(0, secs))
    logger.info('Waking up — market opens in ~%ds', buffer_secs)


# ── Corporate Actions ─────────────────────────────────────────────────────────

def get_upcoming_corporate_actions(symbols: List[str], days_ahead: int = 14) -> Dict[str, List[Dict]]:
    today = datetime.now(timezone.utc)
    since = today.strftime('%Y-%m-%d')
    until = (today + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    ca_types = ['forward_split', 'reverse_split', 'unit_split',
                'merger', 'spinoff', 'cash_merger', 'stock_merger',
                'stock_dividend', 'special_dividend']
    result: Dict[str, List[Dict]] = {}
    for sym in symbols:
        try:
            r = requests.get(
                f'{ALPACA_BASE}/v2/corporate_actions/announcements',
                headers=_headers(),
                params={'ca_types': ','.join(ca_types), 'since': since,
                        'until': until, 'symbol': sym},
                timeout=10,
            )
            if r.status_code == 404:
                continue
            r.raise_for_status()
            actions = r.json()
            if actions:
                result[sym] = actions
        except Exception as e:
            logger.debug('CA lookup failed for %s: %s', sym, e)
    return result


def filter_corporate_action_risks(
    symbols: List[str],
    days_ahead: int = 14,
) -> Tuple[List[str], Dict[str, List[Dict]], Dict[str, Dict]]:
    """
    Returns:
      safe_symbols  — no risky CAs
      risky_dict    — {sym: [actions]} for symbols with splits/mergers
      sentiment_map — {sym: sentiment_dict} for ALL symbols that had CAs
                      (includes safe ones with dividends etc.)
    """
    import time as _t
    _key = (tuple(sorted(symbols)), days_ahead)
    if _key in _ca_filter_cache:
        _res, _ts = _ca_filter_cache[_key]
        if _t.time() - _ts < _CA_CACHE_TTL:
            logger.debug('[CA] Cache hit (%.1fh old)', (_t.time() - _ts) / 3600)
            return _res

    if not symbols:
        return [], {}, {}

    ca_map = get_upcoming_corporate_actions(symbols, days_ahead=days_ahead)

    # Import FinBERT scorer (lazy — only loads model on first call)
    try:
        from engine.data_sources.finbert_sentiment import get_symbol_sentiment
        use_finbert = True
    except Exception as e:
        logger.warning('FinBERT unavailable — skipping sentiment scoring: %s', e)
        use_finbert = False

    safe         = []
    risky        = {}
    sentiment_map: Dict[str, Dict] = {}

    for sym in symbols:
        actions = ca_map.get(sym, [])

        if not actions:
            safe.append(sym)
            continue

        # Score sentiment on ALL CAs for this symbol
        if use_finbert:
            sentiment_map[sym] = get_symbol_sentiment(sym, actions)
        else:
            sentiment_map[sym] = {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0}

        risky_actions = [a for a in actions if a.get('ca_type', '').lower() in RISKY_CA_TYPES]

        if risky_actions:
            sent = sentiment_map[sym]
            risky[sym] = risky_actions
            logger.warning(
                'CA RISK: %s has %d risky event(s) | FinBERT: %s (score=%.2f conf=%.2f)',
                sym, len(risky_actions),
                sent.get('label', '?'), sent.get('score', 0), sent.get('confidence', 0)
            )
        else:
            # Has CAs but not risky (e.g. special dividend) — safe to trade
            safe.append(sym)

    if risky:
        logger.info('CA filter: %d safe / %d risky | sentiment scored: %d',
                    len(safe), len(risky), len(sentiment_map))
    _result = (safe, risky, sentiment_map)
    _ca_filter_cache[_key] = (_result, _t.time())
    return safe, risky, sentiment_map
