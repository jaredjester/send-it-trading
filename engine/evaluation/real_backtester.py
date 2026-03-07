"""
Real historical backtester using Alpaca bars API.
NO pandas/numpy — pure Python only (Pi is RAM constrained).
"""
import os, json, math, logging, requests
from datetime import datetime, timedelta
from pathlib import Path

import alpaca_env
alpaca_env.bootstrap()

def load_keys():
    # Try env vars first, then read .env line by line
    key = os.environ.get('ALPACA_API_LIVE_KEY', '')
    secret = os.environ.get('ALPACA_API_SECRET', '')
    if not key:
        env = Path.home() / 'shared/stockbot/.env'
        if env.exists():
            for line in env.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    if k.strip() == 'ALPACA_API_LIVE_KEY': key = v.strip()
                    elif k.strip() == 'ALPACA_API_SECRET': secret = v.strip()
    return key, secret

def fetch_bars(symbol: str, days: int = 35, offset_days: int = 0) -> list:
    """Fetch daily bars from Alpaca. Returns list of {date, open, high, low, close, volume}.
    offset_days: shift the window back (e.g. offset_days=31 fetches days 31-61 ago for walk-forward training).
    """
    key, secret = load_keys()
    end = datetime.now() - timedelta(days=offset_days)
    start = end - timedelta(days=days)
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {
        'timeframe': '1Day',
        'start': start.strftime('%Y-%m-%dT00:00:00Z'),
        'end': end.strftime('%Y-%m-%dT00:00:00Z'),
        'limit': days,
        'feed': 'iex',
        'adjustment': 'raw'
    }
    headers = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        bars = r.json().get('bars', [])
        return [{'date': b['t'][:10], 'close': b['c'], 'volume': b['v'], 'high': b['h'], 'low': b['l']} for b in bars]
    except Exception as e:
        logging.warning(f"fetch_bars {symbol}: {e}")
        return []

def compute_rsi(closes: list, period: int = 14) -> float:
    """Compute RSI manually from close prices."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def score_bars(bars: list, params: dict) -> list:
    """Score each bar day. Returns list of {date, score, close, volume}."""
    if len(bars) < 22:
        return []
    closes = [b['close'] for b in bars]
    volumes = [b['volume'] for b in bars]
    scored = []
    for i in range(20, len(bars)):
        window_closes = closes[max(0, i-20):i+1]
        window_vols = volumes[max(0, i-20):i+1]
        rsi = compute_rsi(window_closes)
        momentum_5d = (closes[i] - closes[i-5]) / closes[i-5] if closes[i-5] > 0 else 0
        avg_vol = sum(window_vols[:-1]) / len(window_vols[:-1]) if window_vols[:-1] else 1
        vol_ratio = volumes[i] / avg_vol if avg_vol > 0 else 1

        score = 50  # baseline
        if rsi < 35: score += 15
        elif rsi < 45: score += 8
        elif rsi > 65: score -= 10
        elif rsi > 55: score -= 5
        if momentum_5d > 0.02: score += 10
        elif momentum_5d > 0: score += 5
        elif momentum_5d < -0.02: score -= 10
        if vol_ratio > 2.0: score += 10
        elif vol_ratio > 1.5: score += 5

        scored.append({'date': bars[i]['date'], 'score': min(max(score, 0), 100), 'close': closes[i], 'volume': volumes[i]})
    return scored

def run_backtest(symbols: list, days: int, params: dict) -> dict:
    """
    Run parameter combo on historical data.
    params keys: min_score, position_pct, stop_loss (negative float e.g. -0.08)
    Returns: {sharpe, win_rate, total_return, max_drawdown, num_trades, trades}
    """
    min_score = params.get('min_score', 68)
    position_pct = params.get('position_pct', 0.10)
    stop_loss = params.get('stop_loss', -0.08)
    hold_days = params.get('hold_days', 5)

    portfolio = 1000.0
    cash = portfolio
    positions = {}  # symbol -> {entry_price, shares, entry_day, score}
    daily_values = [portfolio]
    trades = []

    offset_days = params.get('offset_days', 0)  # for walk-forward: shift window back

    # Fetch bars for all symbols
    all_scored = {}
    for sym in symbols:
        bars = fetch_bars(sym, days + 10, offset_days=offset_days)
        if bars:
            all_scored[sym] = score_bars(bars, params)

    if not all_scored:
        return {'sharpe': 0, 'win_rate': 0, 'total_return': 0, 'max_drawdown': 0, 'num_trades': 0, 'trades': []}

    # Align to common dates
    max_len = max(len(v) for v in all_scored.values())

    for day_idx in range(max_len):
        # Age positions — exit if held too long or stop loss hit
        to_exit = []
        for sym, pos in positions.items():
            day_age = day_idx - pos['entry_day']
            # Get current price
            sym_data = all_scored.get(sym, [])
            if day_idx < len(sym_data):
                current_price = sym_data[day_idx]['close']
                pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']
                if day_age >= hold_days or pnl_pct <= stop_loss:
                    pnl = pos['shares'] * (current_price - pos['entry_price'])
                    cash += pos['shares'] * current_price
                    trades.append({'symbol': sym, 'pnl_pct': pnl_pct, 'pnl': pnl, 'days_held': day_age, 'exit': 'stop' if pnl_pct <= stop_loss else 'time'})
                    to_exit.append(sym)
        for sym in to_exit:
            del positions[sym]

        # Scan for entries
        if len(positions) < 5:  # max 5 positions
            for sym, scored_days in all_scored.items():
                if sym in positions:
                    continue
                if day_idx >= len(scored_days):
                    continue
                day_data = scored_days[day_idx]
                if day_data['score'] >= min_score and cash > 50:
                    notional = min(portfolio * position_pct, cash * 0.9)
                    entry_price = day_data['close']
                    if entry_price > 0:
                        shares = notional / entry_price
                        cash -= notional
                        positions[sym] = {'entry_price': entry_price, 'shares': shares, 'entry_day': day_idx, 'score': day_data['score']}

        # Mark portfolio value
        pos_value = sum(all_scored[sym][day_idx]['close'] * pos['shares']
                       for sym, pos in positions.items()
                       if sym in all_scored and day_idx < len(all_scored[sym]))
        daily_values.append(cash + pos_value)

    if len(daily_values) < 2 or not trades:
        return {'sharpe': 0, 'win_rate': 0, 'total_return': 0, 'max_drawdown': 0, 'num_trades': 0, 'trades': trades}

    daily_returns = [(daily_values[i] - daily_values[i-1]) / daily_values[i-1] for i in range(1, len(daily_values))]
    mean_r = sum(daily_returns) / len(daily_returns)
    std_r = math.sqrt(sum((r - mean_r)**2 for r in daily_returns) / len(daily_returns)) if len(daily_returns) > 1 else 0.001
    sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0

    wins = [t for t in trades if t['pnl_pct'] > 0]
    win_rate = len(wins) / len(trades) if trades else 0
    total_return = (daily_values[-1] - daily_values[0]) / daily_values[0]

    peak = daily_values[0]
    max_dd = 0
    for v in daily_values:
        if v > peak: peak = v
        dd = (v - peak) / peak
        if dd < max_dd: max_dd = dd

    return {
        'sharpe': round(sharpe, 3),
        'win_rate': round(win_rate, 3),
        'total_return': round(total_return, 4),
        'max_drawdown': round(max_dd, 4),
        'num_trades': len(trades),
        'trades': trades
    }
