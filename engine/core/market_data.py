"""
Unified market data utilities.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_rf_cache = {'value': 0.045, 'ts': 0.0}  # cached risk-free rate (24h TTL)

def get_risk_free_rate() -> float:
    """Approximate risk-free rate from Yahoo (^IRX = 13-week T-bill). Cached 24h."""
    if time.time() - _rf_cache['ts'] < 86400 and _rf_cache['ts'] > 0:
        return _rf_cache['value']
    try:
        import yfinance as yf
        irx = yf.Ticker("^IRX")
        rate = irx.history(period="1d")['Close'].iloc[-1] / 100.0
        if 0.01 <= rate <= 0.10:  # sanity check
            _rf_cache['value'] = rate
            _rf_cache['ts'] = time.time()
            logger.debug('Fetched RF rate: %.4f', rate)
        else:
            logger.warning('Invalid RF rate: %.4f, using cached', rate)
    except Exception as e:
        logger.warning('RF rate fetch failed: %s', e)
    return _rf_cache['value']