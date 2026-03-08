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

# Use unified AlpacaClient
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from engine.core.alpaca_client import AlpacaClient

_client = None

def _get_client() -> AlpacaClient:
    global _client
    if _client is None:
        _client = AlpacaClient()
    return _client


# ── Alpaca live ────────────────────────────────────────────────────────────────

def get_account() -> dict:
    return _get_client().get_account()


def get_positions() -> list:
    return _get_client().get_positions()


def get_spot(symbol: str) -> float:
    """Latest trade price for a stock symbol."""
    return _get_client().get_spot(symbol)


def get_option_chain(symbol: str, expiry: Optional[str] = None) -> List[Dict]:
    """Fetch option contracts for a symbol from Alpaca trading API."""
    contracts = _get_client().get_option_chain(symbol, expiry)
    logger.debug('[DATA] get_option_chain %s: %d contracts', symbol, len(contracts))
    return contracts


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


from engine.core.market_data import get_risk_free_rate


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
