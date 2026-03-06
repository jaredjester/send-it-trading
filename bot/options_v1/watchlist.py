"""
Alpaca Watchlist manager for Options V1.

Keeps a live "Options V1" watchlist on Alpaca that:
  - Is auto-created on first run with the default symbol set
  - Is read each cycle as the source of truth for what to scan
  - Is pruned of non-optionable symbols after the pre-filter runs
  - Can be edited from Alpaca's dashboard / app in real time

Usage:
    wl = WatchlistManager()
    symbols = wl.get_symbols()          # read current list
    wl.remove(symbol)                   # prune bad symbol
    wl.add(symbol)                      # add a new one
    wl.sync(confirmed_symbols)          # replace with a clean list
"""

import os
import logging
import requests
from typing import List, Optional

logger = logging.getLogger(__name__)

ALPACA_BASE = 'https://api.alpaca.markets'
WATCHLIST_NAME = 'Options V1'

DEFAULT_SYMBOLS = [
    'SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA',
    'AMD', 'META', 'MSFT', 'AMZN', 'COIN',
    'GOOGL', 'NFLX', 'UBER', 'BABA', 'PLTR',
]


def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
        'Content-Type': 'application/json',
    }


class WatchlistManager:
    """Thin wrapper around Alpaca's watchlist REST API."""

    def __init__(self):
        self._id: Optional[str] = None
        self._ensure_watchlist()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _ensure_watchlist(self):
        """Find existing 'Options V1' watchlist or create it."""
        try:
            r = requests.get(f'{ALPACA_BASE}/v2/watchlists', headers=_headers(), timeout=10)
            r.raise_for_status()
            for wl in r.json():
                if wl.get('name') == WATCHLIST_NAME:
                    self._id = wl['id']
                    logger.info('WatchlistManager: found existing "%s" (id=%s)', WATCHLIST_NAME, self._id)
                    return
            # Not found — create it
            self._create()
        except Exception as e:
            logger.error('WatchlistManager: failed to fetch watchlists: %s', e)

    def _create(self):
        """Create the Options V1 watchlist with default symbols."""
        try:
            payload = {'name': WATCHLIST_NAME, 'symbols': DEFAULT_SYMBOLS}
            r = requests.post(f'{ALPACA_BASE}/v2/watchlists', headers=_headers(),
                              json=payload, timeout=10)
            r.raise_for_status()
            self._id = r.json()['id']
            logger.info('WatchlistManager: created "%s" with %d symbols (id=%s)',
                        WATCHLIST_NAME, len(DEFAULT_SYMBOLS), self._id)
        except Exception as e:
            logger.error('WatchlistManager: failed to create watchlist: %s', e)

    # ── Public API ───────────────────────────────────────────────────────────

    def get_symbols(self) -> List[str]:
        """Return current symbols in the watchlist. Falls back to DEFAULT_SYMBOLS."""
        if not self._id:
            logger.warning('WatchlistManager: no watchlist id — using defaults')
            return DEFAULT_SYMBOLS
        try:
            r = requests.get(f'{ALPACA_BASE}/v2/watchlists/{self._id}',
                             headers=_headers(), timeout=10)
            r.raise_for_status()
            assets = r.json().get('assets', [])
            symbols = [a['symbol'] for a in assets if a.get('symbol')]
            logger.info('WatchlistManager: loaded %d symbols from "%s"', len(symbols), WATCHLIST_NAME)
            return symbols if symbols else DEFAULT_SYMBOLS
        except Exception as e:
            logger.error('WatchlistManager: get_symbols failed: %s — using defaults', e)
            return DEFAULT_SYMBOLS

    def add(self, symbol: str) -> bool:
        """Add a symbol to the watchlist."""
        if not self._id:
            return False
        try:
            r = requests.post(
                f'{ALPACA_BASE}/v2/watchlists/{self._id}',
                headers=_headers(),
                json={'symbol': symbol},
                timeout=10,
            )
            r.raise_for_status()
            logger.info('WatchlistManager: added %s', symbol)
            return True
        except Exception as e:
            logger.warning('WatchlistManager: add(%s) failed: %s', symbol, e)
            return False

    def remove(self, symbol: str) -> bool:
        """Remove a symbol from the watchlist."""
        if not self._id:
            return False
        try:
            r = requests.delete(
                f'{ALPACA_BASE}/v2/watchlists/{self._id}/{symbol}',
                headers=_headers(),
                timeout=10,
            )
            r.raise_for_status()
            logger.info('WatchlistManager: removed %s (no options available)', symbol)
            return True
        except Exception as e:
            logger.warning('WatchlistManager: remove(%s) failed: %s', symbol, e)
            return False

    def sync(self, confirmed_symbols: List[str]):
        """
        Replace watchlist contents with confirmed_symbols.
        Used after the options pre-filter to keep Alpaca's watchlist
        clean and accurate.
        """
        if not self._id or not confirmed_symbols:
            return
        try:
            r = requests.put(
                f'{ALPACA_BASE}/v2/watchlists/{self._id}',
                headers=_headers(),
                json={'name': WATCHLIST_NAME, 'symbols': confirmed_symbols},
                timeout=10,
            )
            r.raise_for_status()
            logger.info('WatchlistManager: synced watchlist → %d symbols: %s',
                        len(confirmed_symbols), confirmed_symbols)
        except Exception as e:
            logger.error('WatchlistManager: sync failed: %s', e)
