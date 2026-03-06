"""
Synthetic Options Pricing Engine — Options V1

Prices option contracts during pre-market / after-hours by:
  1. Capturing a full IV surface snapshot from the last market session
  2. Sourcing live underlying prices every cycle (Alpaca → Yahoo fallback)
  3. Recomputing theoretical BS prices using live S + cached IV
  4. Estimating bid/ask spreads via historical spread ratios
  5. Bumping IV for known vol events (earnings, macro)
  6. At market open: comparing synthetic vs real price to recalibrate

IV Surface:
  Stored as a nested dict: {symbol: {(strike, expiry_date): iv}}
  Interpolated for arbitrary strikes via nearest-neighbor on strike axis.

Usage:
    pricer = SyntheticPricer(['SPY', 'QQQ', 'AAPL'])
    pricer.refresh_iv_surface()              # call once at startup / daily
    estimates = pricer.price_watchlist()     # reprice all contracts w/ live S
    pricer.convergence_check(real_quotes)    # at open, log IV drift
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

IV_SURFACE_PATH  = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'iv_surface.json'))
SYNTH_PRICES_PATH = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'synthetic_prices.json'))
CONVERGENCE_LOG  = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'convergence_log.jsonl'))

# IV bump multipliers for known vol events (applied to sigma before pricing)
IV_EVENT_BUMPS = {
    'earnings':      1.40,   # +40% IV into earnings
    'fed_decision':  1.15,   # +15% FOMC days
    'cpi':           1.10,   # +10% CPI days
    'default':       1.00,
}

# Historical avg bid/ask spread as % of mid for liquid names
SPREAD_RATIOS = {
    'SPY': 0.02,  'QQQ': 0.02,  'AAPL': 0.03, 'MSFT': 0.03,
    'TSLA': 0.05, 'NVDA': 0.04, 'AMD':  0.05, 'META': 0.04,
    'AMZN': 0.03, 'COIN': 0.08, 'GOOGL': 0.03, 'NFLX': 0.05,
    'UBER': 0.06, 'BABA': 0.07, 'PLTR': 0.06,
}
DEFAULT_SPREAD_RATIO = 0.08


# ── Helpers ───────────────────────────────────────────────────────────────────

def _time_to_expiry(expiry_date: str) -> float:
    """Convert 'YYYY-MM-DD' expiry to years from now (floor at 1/365)."""
    try:
        exp = datetime.strptime(expiry_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs = (exp - now).total_seconds()
        return max(1.0 / 365, secs / (365.25 * 24 * 3600))
    except Exception:
        return 0.1


def _get_live_price(symbol: str) -> Optional[float]:
    """Fetch live/latest stock price. Alpaca first, Yahoo fallback."""
    import os, requests

    # Alpaca latest trade
    try:
        r = requests.get(
            f'https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest',
            headers={
                'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
                'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
            },
            timeout=5,
        )
        if r.status_code == 200:
            price = float(r.json()['trade']['p'])
            if price > 0:
                return price
    except Exception:
        pass

    # Yahoo Finance fallback
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = getattr(info, 'last_price', None) or getattr(info, 'previous_close', None)
        if price and price > 0:
            return float(price)
    except Exception:
        pass

    return None


def _get_risk_free_rate() -> float:
    try:
        import yfinance as yf
        df = yf.download('^IRX', period='5d', progress=False)
        close = df['Close'].squeeze().dropna()
        return float(close.iloc[-1]) / 100.0
    except Exception:
        return 0.045


# ── IV Surface ────────────────────────────────────────────────────────────────

class IVSurface:
    """
    Stores and retrieves implied volatility for (symbol, kind, strike, expiry).
    Refreshed from Alpaca's option snapshots (which include IV).
    Falls back to realized-vol estimate if chain data unavailable.
    """

    def __init__(self):
        self._surface: Dict[str, Dict] = {}
        self._fetched_at: Dict[str, float] = {}
        self._load_from_disk()

    def _load_from_disk(self):
        if IV_SURFACE_PATH.exists():
            try:
                with open(IV_SURFACE_PATH) as f:
                    data = json.load(f)
                self._surface = data.get('surface', {})
                self._fetched_at = data.get('fetched_at', {})
                logger.info('IV surface loaded from disk (%d symbols)', len(self._surface))
            except Exception as e:
                logger.warning('IV surface load failed: %s', e)

    def _save_to_disk(self):
        IV_SURFACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(IV_SURFACE_PATH, 'w') as f:
            json.dump({'surface': self._surface, 'fetched_at': self._fetched_at}, f)

    def refresh(self, symbol: str):
        """
        Fetch IV surface for a symbol.
        Strategy:
          1. Alpaca option snapshots (works during market hours, has live greeks)
          2. yfinance option chain (works 24/7, has IV from last session)
        This means the IV surface is always populated, even at 3 AM.
        """
        loaded = self._refresh_alpaca(symbol)
        if not loaded:
            self._refresh_yfinance(symbol)

    def _refresh_alpaca(self, symbol: str) -> bool:
        """Try to load IV surface from Alpaca snapshots. Returns True if successful."""
        import os, requests
        try:
            r = requests.get(
                f'https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}',
                headers={
                    'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
                    'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
                },
                params={'feed': 'indicative', 'limit': 500},
                timeout=15,
            )
            if r.status_code != 200:
                return False
            snapshots = r.json().get('snapshots', {})
            if not snapshots:
                return False

            contracts = {}
            for occ_key, snap in snapshots.items():
                try:
                    greeks = snap.get('greeks', {}) or {}
                    iv     = greeks.get('iv') or snap.get('impliedVolatility')
                    if not iv or iv < 0.01:
                        continue
                    for i in range(len(occ_key) - 1, -1, -1):
                        if occ_key[i] in ('C', 'P'):
                            break
                    kind_char  = occ_key[i]
                    date_str   = occ_key[i-6:i]
                    exp_date   = datetime.strptime('20' + date_str, '%Y%m%d').strftime('%Y-%m-%d')
                    strike     = int(occ_key[i+1:]) / 1000.0
                    kind       = 'call' if kind_char == 'C' else 'put'
                    contracts[f'{kind}|{strike:.2f}|{exp_date}'] = float(iv)
                except Exception:
                    continue

            if contracts:
                self._surface[symbol]    = contracts
                self._fetched_at[symbol] = time.time()
                self._save_to_disk()
                logger.info('IV surface (Alpaca): %s → %d contracts', symbol, len(contracts))
                return True
            return False
        except Exception as e:
            logger.debug('Alpaca IV surface failed for %s: %s', symbol, e)
            return False

    def _refresh_yfinance(self, symbol: str):
        """
        Load IV surface from yfinance option chains.
        Works 24/7 — serves last-session data when market is closed.
        Loads up to 6 nearest expiries to build a fuller IV surface.
        """
        try:
            import yfinance as yf
            ticker   = yf.Ticker(symbol)
            expiries = ticker.options
            if not expiries:
                logger.warning('yfinance: no expiries for %s', symbol)
                return

            # Load nearest 6 expiries (covers ~2-3 weeks of chains)
            contracts = {}
            loaded_expiries = 0
            for exp_str in expiries[:6]:
                try:
                    chain = ticker.option_chain(exp_str)
                    for kind, df in [('call', chain.calls), ('put', chain.puts)]:
                        for _, row in df.iterrows():
                            iv     = float(row.get('impliedVolatility', 0) or 0)
                            strike = float(row.get('strike', 0) or 0)
                            if iv < 0.05 or strike <= 0:
                                continue   # skip junk IV (deep ITM artifacts)
                            key = f'{kind}|{strike:.2f}|{exp_str}'
                            contracts[key] = iv
                    loaded_expiries += 1
                except Exception as e:
                    logger.debug('yfinance chain failed %s %s: %s', symbol, exp_str, e)

            if contracts:
                self._surface[symbol]    = contracts
                self._fetched_at[symbol] = time.time()
                self._save_to_disk()
                logger.info('IV surface (yfinance): %s → %d contracts across %d expiries',
                            symbol, len(contracts), loaded_expiries)
            else:
                logger.warning('yfinance: no valid IV data for %s', symbol)
        except Exception as e:
            logger.warning('yfinance IV surface failed for %s: %s', symbol, e)

    def get_iv(self, symbol: str, kind: str, strike: float, expiry_date: str,
               fallback_iv: float = 0.30) -> float:
        """
        Get IV for a specific contract. Uses nearest-strike interpolation
        on same expiry / kind. Falls back to fallback_iv if no data.
        """
        sym_surface = self._surface.get(symbol, {})
        if not sym_surface:
            return fallback_iv

        # Exact match first
        exact_key = f'{kind}|{strike:.2f}|{expiry_date}'
        if exact_key in sym_surface:
            return sym_surface[exact_key]

        # Nearest strike on same expiry + kind
        candidates = []
        for key, iv in sym_surface.items():
            parts = key.split('|')
            if len(parts) == 3 and parts[0] == kind and parts[2] == expiry_date:
                try:
                    k = float(parts[1])
                    candidates.append((abs(k - strike), iv))
                except Exception:
                    pass

        if candidates:
            candidates.sort()
            return candidates[0][1]

        # Any expiry / kind nearest strike
        candidates = []
        for key, iv in sym_surface.items():
            parts = key.split('|')
            if len(parts) == 3 and parts[0] == kind:
                try:
                    k = float(parts[1])
                    candidates.append((abs(k - strike), iv))
                except Exception:
                    pass

        if candidates:
            candidates.sort()
            return candidates[0][1]

        return fallback_iv

    def age_hours(self, symbol: str) -> float:
        fetched = self._fetched_at.get(symbol, 0)
        return (time.time() - fetched) / 3600 if fetched else 999.0


# ── Synthetic Pricer ──────────────────────────────────────────────────────────

class SyntheticPricer:
    """
    Pre-market / after-hours option pricer.

    Key methods:
      refresh_iv_surface(symbols)    — update IV cache from Alpaca
      price_contract(...)            — price one contract synthetically
      price_watchlist(symbols)       — reprice all tracked contracts
      get_opening_estimates(symbols) — final pre-open price estimates
      convergence_check(...)         — compare synthetic vs real at open
    """

    def __init__(self):
        self.iv_surface = IVSurface()
        self._rf = None
        self._rf_fetched = 0.0
        self._spot_cache: Dict[str, Tuple[float, float]] = {}  # sym → (price, ts)

    # ── Risk-free rate (cached 1h) ────────────────────────────────────────────
    def get_rf(self) -> float:
        if time.time() - self._rf_fetched > 3600:
            self._rf = _get_risk_free_rate()
            self._rf_fetched = time.time()
        return self._rf or 0.045

    # ── Live spot (cached 5 min) ──────────────────────────────────────────────
    def get_spot(self, symbol: str) -> Optional[float]:
        cached = self._spot_cache.get(symbol)
        if cached and time.time() - cached[1] < 300:
            return cached[0]
        price = _get_live_price(symbol)
        if price:
            self._spot_cache[symbol] = (price, time.time())
        return price

    # ── IV Surface refresh ────────────────────────────────────────────────────
    def refresh_iv_surface(self, symbols: List[str], force: bool = False):
        """
        Refresh IV surface for all symbols.
        Skips if data is <4h old unless force=True.
        """
        refreshed = []
        for sym in symbols:
            age = self.iv_surface.age_hours(sym)
            if force or age > 4.0:
                self.iv_surface.refresh(sym)
                refreshed.append(sym)
        if refreshed:
            logger.info('IV surface refreshed for: %s', refreshed)
        else:
            logger.info('IV surface up-to-date for all %d symbols', len(symbols))

    # ── Single contract pricing ───────────────────────────────────────────────
    def price_contract(
        self,
        symbol: str,
        kind: str,
        strike: float,
        expiry_date: str,
        S: Optional[float] = None,
        event_type: str = 'default',
    ) -> Dict:
        """
        Compute synthetic BS price + Greeks for one contract.

        Returns dict with:
          synthetic_price, bid_estimate, ask_estimate,
          delta, gamma, vega, theta,
          iv_used, S_used, T_used, event_bump
        """
        from options_v1.pricing import bs_price, bs_greeks

        # Live spot
        if S is None:
            S = self.get_spot(symbol)
        if S is None or S <= 0:
            return {'error': 'no spot price', 'symbol': symbol}

        # Time to expiry
        T = _time_to_expiry(expiry_date)
        if T <= 0:
            return {'error': 'expired', 'symbol': symbol}

        # IV from surface
        rf = self.get_rf()
        base_iv = self.iv_surface.get_iv(symbol, kind, strike, expiry_date)

        # Event IV bump
        event_bump = IV_EVENT_BUMPS.get(event_type, IV_EVENT_BUMPS['default'])
        sigma = base_iv * event_bump

        # BS price + Greeks
        price  = bs_price(kind, S, strike, T, rf, sigma)
        greeks = bs_greeks(kind, S, strike, T, rf, sigma)

        # Bid/ask spread estimation
        spread_ratio = SPREAD_RATIOS.get(symbol, DEFAULT_SPREAD_RATIO)
        half_spread  = price * spread_ratio / 2
        bid = max(0.01, price - half_spread)
        ask = price + half_spread

        return {
            'symbol':          symbol,
            'kind':            kind,
            'strike':          strike,
            'expiry_date':     expiry_date,
            'synthetic_price': round(price, 4),
            'bid_estimate':    round(bid, 4),
            'ask_estimate':    round(ask, 4),
            'delta':           round(greeks['delta'], 4),
            'gamma':           round(greeks['gamma'], 6),
            'vega':            round(greeks['vega'], 4),
            'theta':           round(greeks['theta'], 4),
            'iv_used':         round(sigma, 4),
            'base_iv':         round(base_iv, 4),
            'event_bump':      event_bump,
            'S_used':          round(S, 2),
            'T_years':         round(T, 4),
            'rf':              round(rf, 4),
            'timestamp':       datetime.now(timezone.utc).isoformat(),
        }

    # ── Reprice full watchlist ────────────────────────────────────────────────
    def price_watchlist(
        self,
        symbols: List[str],
        event_map: Dict[str, str] = None,
    ) -> Dict[str, List[Dict]]:
        """
        For each symbol, reprice every tracked contract in the IV surface.
        Returns {symbol: [priced_contract, ...]}

        event_map: {symbol: event_type} for IV bumping
        """
        event_map = event_map or {}
        results   = {}
        total     = 0

        for sym in symbols:
            sym_surface = self.iv_surface._surface.get(sym, {})
            if not sym_surface:
                logger.debug('No IV surface for %s — skipping reprice', sym)
                continue

            S = self.get_spot(sym)
            if not S:
                logger.warning('No spot price for %s — skipping reprice', sym)
                continue

            event_type = event_map.get(sym, 'default')
            priced     = []

            for key in sym_surface:
                parts = key.split('|')
                if len(parts) != 3:
                    continue
                kind, strike_str, expiry_date = parts
                try:
                    strike = float(strike_str)
                except ValueError:
                    continue

                result = self.price_contract(sym, kind, strike, expiry_date,
                                              S=S, event_type=event_type)
                if 'error' not in result:
                    priced.append(result)

            if priced:
                results[sym] = priced
                total += len(priced)
                logger.info('Repriced %d contracts for %s @ S=%.2f', len(priced), sym, S)

        logger.info('Synthetic reprice complete: %d contracts across %d symbols', total, len(results))
        self._save_synthetic_prices(results)
        return results

    # ── Opening estimates ─────────────────────────────────────────────────────
    def get_opening_estimates(
        self,
        symbols: List[str],
        event_map: Dict[str, str] = None,
    ) -> Dict[str, Dict]:
        """
        Final reprice pass ~5 min before open using freshest spot prices.
        Returns top ATM/near-ATM contracts per symbol sorted by EV proxy (delta * vega).
        """
        logger.info('=== OPENING ESTIMATES (pre-market final reprice) ===')
        # Force fresh spot prices
        for sym in symbols:
            if sym in self._spot_cache:
                del self._spot_cache[sym]

        all_prices = self.price_watchlist(symbols, event_map=event_map)
        estimates  = {}

        for sym, contracts in all_prices.items():
            if not contracts:
                continue
            S = self.get_spot(sym) or 0
            # Rank by |delta| proximity to 0.30-0.50 (sweet spot for directional buys)
            def rank_key(c):
                d = abs(c.get('delta', 0))
                return abs(d - 0.35)   # target ~0.35 delta

            sorted_c = sorted(contracts, key=rank_key)
            top5 = sorted_c[:5]

            estimates[sym] = {
                'spot':      S,
                'top_calls': [c for c in top5 if c['kind'] == 'call'][:3],
                'top_puts':  [c for c in top5 if c['kind'] == 'put'][:3],
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            logger.info('Opening estimate %s @ S=%.2f | best call: %s | best put: %s',
                sym, S,
                top5[0]['strike'] if top5 else 'n/a',
                next((c['strike'] for c in top5 if c['kind'] == 'put'), 'n/a'),
            )

        return estimates

    # ── Convergence check ─────────────────────────────────────────────────────
    def convergence_check(self, real_quotes: Dict[str, float]):
        """
        At market open, compare synthetic prices to real market quotes.
        Logs IV drift and recalibrates the surface.

        real_quotes: {occ_symbol: real_mid_price}
        """
        if not real_quotes:
            return

        drifts = []
        for occ_sym, real_price in real_quotes.items():
            if real_price <= 0:
                continue
            try:
                # Parse OCC symbol back to components
                for i in range(len(occ_sym) - 1, -1, -1):
                    if occ_sym[i] in ('C', 'P'):
                        break
                sym       = occ_sym[:i-6]
                kind      = 'call' if occ_sym[i] == 'C' else 'put'
                date_str  = occ_sym[i-6:i]
                exp_date  = datetime.strptime('20' + date_str, '%Y%m%d').strftime('%Y-%m-%d')
                strike    = int(occ_sym[i+1:]) / 1000.0

                S = self.get_spot(sym)
                if not S:
                    continue

                est = self.price_contract(sym, kind, strike, exp_date, S=S)
                if 'error' in est:
                    continue

                synthetic = est['synthetic_price']
                drift_pct = (real_price - synthetic) / synthetic * 100 if synthetic > 0 else 0

                drifts.append({
                    'symbol':       sym,
                    'occ':          occ_sym,
                    'synthetic':    synthetic,
                    'real':         real_price,
                    'drift_pct':    round(drift_pct, 2),
                    'iv_used':      est['iv_used'],
                    'timestamp':    datetime.now(timezone.utc).isoformat(),
                })

                logger.info('Convergence %s: synthetic=%.3f real=%.3f drift=%.1f%%',
                            occ_sym, synthetic, real_price, drift_pct)
            except Exception as e:
                logger.debug('Convergence parse failed for %s: %s', occ_sym, e)

        if drifts:
            # Append to convergence log
            CONVERGENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(CONVERGENCE_LOG, 'a') as f:
                for d in drifts:
                    f.write(json.dumps(d) + '\n')

            avg_drift = sum(d['drift_pct'] for d in drifts) / len(drifts)
            logger.info('Convergence check: %d contracts | avg drift=%.1f%%', len(drifts), avg_drift)

            # If systematic drift >10%, force IV surface refresh
            if abs(avg_drift) > 10:
                syms = list({d['symbol'] for d in drifts})
                logger.warning('Large IV drift detected (%.1f%%) — forcing IV surface refresh: %s',
                               avg_drift, syms)
                self.refresh_iv_surface(syms, force=True)

    def _save_synthetic_prices(self, results: Dict):
        SYNTH_PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SYNTH_PRICES_PATH, 'w') as f:
            json.dump({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'prices':    results,
            }, f)
