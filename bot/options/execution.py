"""Execution layer: Alpaca options order routing."""
import os
import logging
import requests
from typing import Optional

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


def _post(endpoint: str, payload: dict) -> dict:
    r = requests.post(f'{ALPACA_BASE}{endpoint}', headers=HEADERS, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _delete(endpoint: str) -> dict:
    r = requests.delete(f'{ALPACA_BASE}{endpoint}', headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json() if r.text else {}


def submit_option_order(
    symbol: str,
    contracts: int,
    side: str,           # 'buy' or 'sell'
    order_type: str = 'market',
    limit_price: Optional[float] = None,
    time_in_force: str = 'day',
) -> dict:
    """Submit an options order via Alpaca. symbol = OCC option symbol."""
    payload = {
        'symbol':        symbol,
        'qty':           str(contracts),
        'side':          side,
        'type':          order_type,
        'time_in_force': time_in_force,
    }
    if order_type == 'limit' and limit_price is not None:
        payload['limit_price'] = str(round(limit_price, 2))

    try:
        response = _post('/v2/orders', payload)
        logger.info('Order submitted: %s %s %s x%s -> id=%s',
                    side, order_type, symbol, contracts, response.get('id'))
        return response
    except Exception as e:
        logger.error('Order submission failed for %s: %s', symbol, e)
        raise



def cancel_all_orders() -> dict:
    return _delete('/v2/orders')


def close_position(symbol: str) -> dict:
    """Market close all contracts for a given option symbol."""
    try:
        return _delete(f'/v2/positions/{symbol}')
    except Exception as e:
        logger.error('Failed to close position %s: %s', symbol, e)
        raise




def submit_gtc_exit_order(occ_symbol: str, contracts: int,
                          limit_price: float, side: str = 'sell') -> dict:
    """Submit GTC limit exit order (take-profit or stop-limit) to Alpaca."""
    price = round(limit_price, 2)
    if price <= 0:
        logger.warning('[EXEC] GTC exit skipped — invalid price %.2f for %s', price, occ_symbol)
        return {}
    payload = {
        'symbol':        occ_symbol,
        'qty':           str(contracts),
        'side':          side,
        'type':          'limit',
        'time_in_force': 'day',  # Alpaca options: only 'day' supported (not gtc)
        'limit_price':   str(price),
    }
    try:
        r = requests.post(f'{ALPACA_BASE}/v2/orders', headers=HEADERS, json=payload, timeout=10)
        r.raise_for_status()
        order = r.json()
        logger.info('[EXEC] GTC %s order placed: %s x%d @ $%.2f | id=%s',
                    side.upper(), occ_symbol, contracts, price, order.get('id', '?')[:8])
        return order
    except Exception as e:
        logger.warning('[EXEC] GTC exit order failed for %s: %s', occ_symbol, e)
        return {}


def verify_position_closed(occ_symbol: str, retries: int = 3, delay: float = 3.0) -> bool:
    """Poll Alpaca to verify a position was actually closed. Returns True if gone."""
    import time as _t
    for i in range(retries):
        try:
            r = requests.get(
                f'{ALPACA_BASE}/v2/positions/{occ_symbol}',
                headers=HEADERS, timeout=8,
            )
            if r.status_code == 404:
                logger.info('[EXEC] Position %s confirmed closed', occ_symbol)
                return True
            if r.status_code == 200:
                pos = r.json()
                qty = float(pos.get('qty', 1))
                if qty == 0:
                    return True
                logger.debug('[EXEC] Position %s still open (qty=%.0f), retry %d/%d',
                            occ_symbol, qty, i + 1, retries)
        except Exception as e:
            logger.debug('[EXEC] verify_position_closed %s: %s', occ_symbol, e)
        if i < retries - 1:
            _t.sleep(delay)
    logger.warning('[EXEC] Could not verify %s closed after %d retries', occ_symbol, retries)
    return False


def get_open_orders() -> list:
    """Fetch all open/pending orders from Alpaca."""
    try:
        r = requests.get(
            f'{ALPACA_BASE}/v2/orders',
            headers=HEADERS,
            params={'status': 'open', 'limit': 50},
            timeout=8,
        )
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        logger.warning('[EXEC] get_open_orders failed: %s', e)
        return []


def cancel_order(order_id: str) -> bool:
    """Cancel an open order by ID."""
    try:
        r = requests.delete(
            f'{ALPACA_BASE}/v2/orders/{order_id}',
            headers=HEADERS, timeout=8,
        )
        if r.status_code in (200, 204):
            logger.info('[EXEC] Cancelled order %s', order_id[:8])
            return True
        logger.warning('[EXEC] Cancel order %s: HTTP %d', order_id[:8], r.status_code)
        return False
    except Exception as e:
        logger.warning('[EXEC] cancel_order %s: %s', order_id[:8], e)
        return False


def get_market_quote(occ_symbol: str) -> dict:
    """Fetch live bid/ask/midpoint for an option via Alpaca snapshot."""
    try:
        r = requests.get(
            f'{ALPACA_DATA}/v1beta1/options/snapshots',
            headers=HEADERS,
            params={'symbols': occ_symbol, 'feed': 'indicative'},
            timeout=8,
        )
        r.raise_for_status()
        snap = r.json().get('snapshots', {}).get(occ_symbol, {})
        q    = snap.get('latestQuote', {})
        bid  = float(q.get('bp', 0) or 0)
        ask  = float(q.get('ap', 0) or 0)
        mid  = round((bid + ask) / 2, 2) if bid and ask else 0.0
        iv   = snap.get('impliedVolatility', 0)
        return {'bid': bid, 'ask': ask, 'mid': mid, 'iv': iv, 'ok': bool(ask > 0)}
    except Exception as e:
        logger.warning('[EXEC] get_market_quote %s: %s', occ_symbol, e)
        return {'bid': 0, 'ask': 0, 'mid': 0, 'iv': 0, 'ok': False}

def build_occ_symbol(underlying: str, expiry: str, kind: str, strike: float) -> str:
    """Build OCC option symbol. expiry = YYMMDD string."""
    side = 'C' if kind == 'call' else 'P'
    strike_str = f'{int(strike * 1000):08d}'
    return f'{underlying.upper()}{expiry}{side}{strike_str}'


def get_open_option_orders() -> list:
    r = requests.get(f'{ALPACA_BASE}/v2/orders',
                     headers=HEADERS,
                     params={'status': 'open', 'limit': 100},
                     timeout=10)
    r.raise_for_status()
    return r.json()
