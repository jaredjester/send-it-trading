"""Data ingestion: Alpaca live options + Yahoo Finance historical."""
import os
import json
import time
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

ALPACA_KEY    = os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', ''))
ALPACA_SECRET = os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', ''))
ALPACA_BASE   = 'https://api.alpaca.markets'
ALPACA_DATA   = 'https://data.alpaca.markets'

HEADERS = {
    'APCA-API-KEY-ID': ALPACA_KEY,
    'APCA-API-SECRET-KEY': ALPACA_SECRET,
    'Content-Type': 'application/json',
}


def _get(url: str, params: dict = None) -> dict:
    r = requests.get(url, headers=HEADERS, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Alpaca live ────────────────────────────────────────────────────────────────

def get_account() -> dict:
    return _get(f'{ALPACA_BASE}/v2/account')


def get_positions() -> list:
    return _get(f'{ALPACA_BASE}/v2/positions')


def get_spot(symbol: str) -> float:
    """Latest trade price for a stock symbol."""
    data = _get(f'{ALPACA_DATA}/v2/stocks/{symbol}/trades/latest')
    return float(data['trade']['p'])


def get_option_chain(symbol: str, expiry: Optional[str] = None) -> List[Dict]:
    """Fetch option contracts for a symbol from Alpaca trading API."""
    today    = datetime.utcnow().date()
    min_exp  = (today + timedelta(days=1)).isoformat()   # skip same-day expiries
    max_exp  = (today + timedelta(days=60)).isoformat()
    params = {
        'underlying_symbols':  symbol,
        'expiration_date_gte': min_exp,
        'expiration_date_lte': max_exp,
        'limit': 200,
    }
    if expiry:
        params['expiration_date'] = expiry
        params.pop('expiration_date_gte', None)
        params.pop('expiration_date_lte', None)
    try:
        data = _get(f'{ALPACA_BASE}/v2/options/contracts', params)
        contracts = data.get('option_contracts', [])
        logger.debug('[DATA] get_option_chain %s: %d contracts', symbol, len(contracts))
        return contracts
    except Exception as e:
        logger.warning('[DATA] get_option_chain %s: %s — using empty chain', symbol, e)
        return []


def get_option_snapshot(symbol_or_occs) -> Dict:
    """Latest quotes + greeks for options.
    Pass a ticker ('SPY') to get ATM snapshots, or a list of OCC symbols.
    """
    # If given a plain ticker, first fetch the near-term contracts
    if isinstance(symbol_or_occs, str) and not any(c.isdigit() for c in symbol_or_occs):
        contracts = get_option_chain(symbol_or_occs)
        if not contracts:
            return {}
        occ_syms = [c['symbol'] for c in contracts[:50]]
    elif isinstance(symbol_or_occs, list):
        occ_syms = symbol_or_occs[:50]
    else:
        occ_syms = [symbol_or_occs]

    if not occ_syms:
        return {}
    try:
        params = {'symbols': ','.join(occ_syms), 'feed': 'indicative'}
        data   = _get(f'{ALPACA_DATA}/v1beta1/options/snapshots', params)
        snaps  = data.get('snapshots', {})
        logger.debug('[DATA] snapshots: %d/%d returned', len(snaps), len(occ_syms))
        return snaps
    except Exception as e:
        logger.warning('[DATA] get_option_snapshot: %s', e)
        return {}


# ── Yahoo Finance historical ───────────────────────────────────────────────────

def get_historical_ohlcv(symbol: str, period: str = '1y') -> pd.DataFrame:
    """Pull daily OHLCV from Yahoo Finance."""
    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logger.error('Yahoo historical failed for %s: %s', symbol, e)
        return pd.DataFrame()


def get_historical_iv(symbol: str) -> float:
    """Approximate realized vol as a proxy for IV baseline."""
    df = get_historical_ohlcv(symbol, period='3mo')
    if df.empty:
        return 0.3
    # yfinance >=0.2 returns MultiIndex columns — squeeze to plain Series
    close = df['Close'].squeeze()
    log_returns = np.log(close / close.shift(1)).dropna()
    annual_vol = float(log_returns.std() * np.sqrt(252))
    return annual_vol


_rf_cache = {'value': 0.045, 'ts': 0.0}  # cached risk-free rate (24h TTL)

def get_risk_free_rate() -> float:
    """Approximate risk-free rate from Yahoo (^IRX = 13-week T-bill). Cached 24h."""
    import time as _t
    if _t.time() - _rf_cache['ts'] < 86400 and _rf_cache['ts'] > 0:
        return _rf_cache['value']
    try:
        import yfinance as yf
        df = yf.download('^IRX', period='5d', progress=False)
        close = df['Close'].squeeze().dropna()
        rate = float(close.iloc[-1]) / 100.0
        _rf_cache.update({'value': rate, 'ts': _t.time()})
        logger.info('[DATA] RF rate refreshed: %.4f', rate)
        return rate
    except Exception as e:
        logger.debug('[DATA] RF fetch failed: %s — using cached %.4f', e, _rf_cache['value'])
        return _rf_cache['value']


# ── Convenience bundle ─────────────────────────────────────────────────────────

def market_data_bundle(symbol: str) -> Dict[str, Any]:
    """Returns a dict with spot, IV, risk-free rate, and option chain."""
    spot  = get_spot(symbol)
    iv    = get_historical_iv(symbol)
    rf    = get_risk_free_rate()
    try:
        chain = get_option_chain(symbol)
    except Exception as e:
        logger.warning('[DATA] chain fetch failed for %s: %s — proceeding without chain', symbol, e)
        chain = []
    return {'symbol': symbol, 'spot': spot, 'iv': iv, 'rf': rf, 'chain': chain}
