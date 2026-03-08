"""
Gamma Squeeze Scanner for Options V1
=====================================
Computes Dealer Gamma Exposure (GEX) across the full options chain,
detects gamma walls, gamma flip level, and squeeze probability score.

How it works:
  GEX_strike = (call_OI - put_OI) * gamma * spot^2 * multiplier
  Total_GEX  = sum(GEX_strike) across all strikes

  Total_GEX > 0 → dealers long gamma → stabilize price (mean reversion)
  Total_GEX < 0 → dealers short gamma → amplify moves (momentum/squeeze)

Gamma Flip = strike where cumulative GEX crosses zero
Gamma Wall  = strike with highest absolute GEX (pinning level)

Squeeze Score (0–100):
  combines negative GEX intensity + momentum + volume

Usage:
    scanner = GammaScanner()
    result  = scanner.scan('SPY', spot=680.0)
    # result.squeeze_score, result.regime, result.gamma_flip, result.walls
"""
import os
import math
import time
import json
import logging
import requests
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ALPACA_BASE  = 'https://api.alpaca.markets'
ALPACA_DATA  = 'https://data.alpaca.markets'
MULTIPLIER   = 100       # equity options: 100 shares per contract
CACHE_SECS   = 300       # 5-min cache for GEX results
CACHE_FILE   = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'gex_cache.json'))

# Squeeze thresholds (rough SPY-scale; normalised per-symbol below)
NEGATIVE_GEX_THRESHOLD  = -0.5e9   # $-500M notional adjusted gamma = squeeze risk
SQUEEZE_SCORE_THRESHOLD = 55        # 0-100, above = squeeze mode


def _headers() -> dict:
    return {
        'APCA-API-KEY-ID':     os.getenv('ALPACA_API_LIVE_KEY', os.getenv('APCA_API_KEY_ID', '')),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_API_SECRET',   os.getenv('APCA_API_SECRET_KEY', '')),
    }


# ── Black-Scholes Greeks (consolidated in engine/core/pricing.py) ──────────────
from engine.core.pricing import bs_gamma, bs_delta


def _bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes vanna = ∂Δ/∂σ = -N'(d1) * d2 / sigma
    Measures how dealer delta changes when IV changes.
    Filters: skip near-expiry (<2 DTE) to avoid vanna singularities.
    """
    if T <= 2/365 or sigma <= 0 or S <= 0:   # filter <2 DTE noise
        return 0.0
    try:
        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        n_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)   # standard normal PDF
        return -n_d1 * d2 / sigma
    except (ValueError, ZeroDivisionError):
        return 0.0


def _bs_charm(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Black-Scholes charm = ∂Δ/∂t (delta decay per day).
    charm = -N'(d1) * [2*r*T - d2*sigma*sqrt(T)] / (2*T*sigma*sqrt(T))

    Positive charm → delta rising with time → dealer buying pressure
    Negative charm → delta falling with time → dealer selling pressure
    Filter: skip <2 DTE (charm explodes near expiry for gamma positions)
    """
    if T <= 2/365 or sigma <= 0 or S <= 0:
        return 0.0
    try:
        sqrt_T  = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        n_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        charm = -n_d1 * (2 * r * T - d2 * sigma * sqrt_T) / (2 * T * sigma * sqrt_T)
        return charm / 365  # convert to per-calendar-day
    except (ValueError, ZeroDivisionError):
        return 0.0


def _time_to_expiry(expiry_str: str) -> float:
    """Fraction of year to expiry. Options expire at 4 PM ET = 21:00 UTC."""
    try:
        from datetime import timedelta
        exp = datetime.strptime(expiry_str[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(hours=21)
        secs = (exp - datetime.now(timezone.utc)).total_seconds()
        return max(1.0/365, secs / (365.25 * 86400))
    except Exception:
        return 0.0


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class StrikeGEX:
    strike:    float
    gex:       float       # signed GEX at this strike ($-notional)
    vex:       float = 0.0 # signed VEX at this strike ($-notional)
    call_oi:   int = 0
    put_oi:    int = 0
    gamma:     float = 0.0
    vanna:     float = 0.0
    charm:     float = 0.0
    cex:       float = 0.0
    expiry:    str = ''


@dataclass
class GammaProfile:
    symbol:              str
    spot:                float
    total_gex:           float          # signed total GEX
    gex_per_billion:     float          # normalized
    gamma_flip:          Optional[float]  # strike where GEX changes sign
    positive_wall:       Optional[float]  # strike with most positive GEX
    negative_wall:       Optional[float]  # strike with most negative GEX
    nearest_wall:        Optional[float]  # closest wall to spot
    wall_distance_pct:   float          # % from spot to nearest wall
    regime:              str            # 'positive_gamma' | 'negative_gamma' | 'neutral'
    # VEX fields
    total_vex:           float = 0.0   # signed total Vanna Exposure
    vex_per_billion:     float = 0.0
    vex_regime:          str = 'neutral'  # 'positive_vanna' | 'negative_vanna' | 'neutral'
    # CEX
    total_cex:           float = 0.0   # signed Charm Exposure
    cex_per_billion:     float = 0.0
    cex_regime:          str = 'neutral'  # 'positive_charm' | 'negative_charm' | 'neutral'
    # Combined dealer pressure (GEX + VEX + CEX weighted)
    dealer_pressure:     float = 0.0   # signed: + = buy pressure, - = sell pressure
    # Combined squeeze
    squeeze_score:       float = 0.0   # 0–100 combined GEX+VEX+CEX
    squeeze_active:      bool = False
    momentum_z:          float = 0.0   # how many σ above realized vol
    strikes:             List[StrikeGEX] = field(default_factory=list)
    timestamp:           str = ''''''   

    def to_dict(self) -> dict:
        d = asdict(self)
        d['strikes'] = [{k: v for k, v in s.items()} for s in d['strikes']]
        return d

    def summary(self) -> str:
        return (f"GEX={self.gex_per_billion:+.2f}B/{self.regime[:3]}  "
                f"VEX={self.vex_per_billion:+.2f}B  CEX={self.cex_per_billion:+.2f}B  "
                f"pressure={self.dealer_pressure:+.2f}B  "
                f"flip={self.gamma_flip}  squeeze={self.squeeze_score:.0f}/100")


# ── Scanner ───────────────────────────────────────────────────────────────────

class GammaScanner:
    """Compute and cache GEX profiles for options watchlist symbols."""

    def __init__(self, rf: float = 0.045):
        self._rf    = rf
        self._cache: Dict[str, Tuple[float, GammaProfile]] = {}   # sym -> (ts, profile)

    # ── Public API ──────────────────────────────────────────────────────────

    def scan(self, symbol: str, spot: float, iv_fallback: float = 0.30,
             momentum_z: float = 0.0, force: bool = False) -> Optional[GammaProfile]:
        """
        Compute full GEX profile for a symbol.
        Returns cached result if <CACHE_SECS old.
        """
        cached = self._cache.get(symbol)
        if not force and cached and (time.time() - cached[0]) < CACHE_SECS:
            return cached[1]

        chain = self._fetch_chain(symbol)
        if not chain:
            logger.debug('[GEX] No chain data for %s', symbol)
            return None

        profile = self._compute_profile(symbol, spot, chain, iv_fallback, momentum_z)
        self._cache[symbol] = (time.time(), profile)
        self._persist(symbol, profile)
        return profile

    def scan_watchlist(self, symbols: List[str], spot_map: Dict[str, float],
                       iv_map: Dict[str, float] = None,
                       momentum_map: Dict[str, float] = None) -> Dict[str, GammaProfile]:
        """Scan entire watchlist. Returns {symbol: GammaProfile}."""
        results = {}
        for sym in symbols:
            spot = spot_map.get(sym)
            if not spot:
                continue
            iv  = (iv_map or {}).get(sym, 0.30)
            mz  = (momentum_map or {}).get(sym, 0.0)
            try:
                p = self.scan(sym, spot, iv_fallback=iv, momentum_z=mz)
                if p:
                    results[sym] = p
                    logger.info('[GEX] %s %s', sym, p.summary())
            except Exception as e:
                logger.warning('[GEX] scan(%s) failed: %s', sym, e)
        return results

    def get_rl_features(self, symbol: str) -> Dict[str, float]:
        """
        Return the 6 GEX features to add to the RL state vector.
        Returns zeros if no profile cached.
        """
        cached = self._cache.get(symbol)
        if not cached:
            return {
                'gex_billion':           0.0,
                'gex_regime':            0.0,   # +1 positive, -1 negative, 0 neutral
                'vex_billion':           0.0,
                'vex_regime':            0.0,
                'cex_billion':           0.0,
                'cex_regime':            0.0,   # +1 pos charm, -1 neg charm
                'dealer_pressure':       0.0,
                'gamma_flip_dist_pct':   0.0,
                'gamma_wall_dist_pct':   0.0,
                'squeeze_score':         0.0,
                'squeeze_active':        0.0,
            }
        p = cached[1]
        regime_enc = {'positive_gamma': 1.0, 'negative_gamma': -1.0, 'neutral': 0.0}
        vex_enc    = {'positive_vanna': 1.0, 'negative_vanna': -1.0, 'neutral': 0.0}
        flip_dist = 0.0
        if p.gamma_flip and p.spot:
            flip_dist = (p.gamma_flip - p.spot) / p.spot * 100
        return {
            'gex_billion':          round(p.gex_per_billion, 3),
            'gex_regime':           regime_enc.get(p.regime, 0.0),
            'vex_billion':          round(p.vex_per_billion, 3),
            'vex_regime':           vex_enc.get(p.vex_regime, 0.0),
            'cex_billion':          round(p.cex_per_billion, 3),
            'cex_regime':           (1.0 if p.cex_regime == 'positive_charm' else
                                     -1.0 if p.cex_regime == 'negative_charm' else 0.0),
            'dealer_pressure':      round(p.dealer_pressure, 3),
            'gamma_flip_dist_pct':  round(flip_dist, 2),
            'gamma_wall_dist_pct':  round(p.wall_distance_pct, 2),
            'squeeze_score':        round(p.squeeze_score, 1),
            'squeeze_active':       1.0 if p.squeeze_active else 0.0,
        }

    # ── Internal computation ─────────────────────────────────────────────────

    def _fetch_chain(self, symbol: str) -> List[dict]:
        """Fetch options chain from Alpaca with OI and IV per contract."""
        try:
            from datetime import timedelta
            # Skip today 0DTE (no VEX signal, vanna singularities)
            # Fetch next 45 days across full term structure
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
            exp_max  = (datetime.now(timezone.utc) + timedelta(days=45)).strftime('%Y-%m-%d')
            params = {
                'underlying_symbols': symbol,
                'limit': 200,
                'expiration_date_gte': tomorrow,
                'expiration_date_lte': exp_max,
            }
            r = requests.get(f'{ALPACA_BASE}/v2/options/contracts',
                             headers=_headers(), params=params, timeout=10)
            if not r.ok:
                return []
            contracts = r.json().get('option_contracts', [])
            return [c for c in contracts
                    if c.get('open_interest') and float(c.get('open_interest', 0)) > 0]
        except Exception as e:
            logger.warning('[GEX] chain fetch failed for %s: %s', symbol, e)
            return []

    def _compute_profile(self, symbol: str, spot: float, chain: List[dict],
                         iv_fallback: float, momentum_z: float) -> GammaProfile:
        """Core GEX computation from chain data."""
        # Group by strike
        strikes_map: Dict[float, Dict] = {}
        for c in chain:
            K     = float(c.get('strike_price', 0))
            oi    = int(float(c.get('open_interest', 0)))
            expiry = c.get('expiration_date', '')
            kind  = c.get('type', c.get('option_type', '')).lower()
            T     = _time_to_expiry(expiry)
            if T <= 0 or K <= 0:
                continue

            # Use close_price to back out IV if available, else use fallback
            close = float(c.get('close_price') or 0)
            iv = iv_fallback  # TODO: back out from close with BS inversion

            gamma = bs_gamma(spot, K, T, self._rf, iv)
            gex_contribution = oi * gamma * spot ** 2 * MULTIPLIER

            vanna   = _bs_vanna(spot, K, T, self._rf, iv)
            charm   = _bs_charm(spot, K, T, self._rf, iv)
            # VEX: OI * vanna * spot * multiplier (sign: calls +, puts -)
            vex_contribution = oi * vanna * spot * MULTIPLIER
            # CEX: OI * charm * spot * multiplier
            # Positive CEX → time decay → dealers BUY (upward drift)
            cex_contribution = oi * charm * spot * MULTIPLIER

            if K not in strikes_map:
                strikes_map[K] = {'call_oi': 0, 'put_oi': 0,
                                   'gamma': gamma, 'vanna': vanna, 'charm': charm,
                                   'expiry': expiry, 'gex': 0.0, 'vex': 0.0, 'cex': 0.0}

            if kind == 'call':
                strikes_map[K]['call_oi'] += oi
                strikes_map[K]['gex'] += gex_contribution
                strikes_map[K]['vex'] += vex_contribution
                strikes_map[K]['cex'] += cex_contribution   # calls: positive charm
            else:
                strikes_map[K]['put_oi'] += oi
                strikes_map[K]['gex'] -= gex_contribution
                strikes_map[K]['vex'] -= vex_contribution
                strikes_map[K]['cex'] -= cex_contribution   # puts: negative charm

        if not strikes_map:
            return self._empty_profile(symbol, spot)

        # Build strike list sorted by strike
        strike_list = sorted([
            StrikeGEX(
                strike=K,
                gex=v['gex'],
                vex=v.get('vex', 0.0),
                cex=v.get('cex', 0.0),
                call_oi=v['call_oi'],
                put_oi=v['put_oi'],
                gamma=v['gamma'],
                vanna=v.get('vanna', 0.0),
                charm=v.get('charm', 0.0),
                expiry=v['expiry'],
            )
            for K, v in strikes_map.items()
        ], key=lambda x: x.strike)

        total_gex = sum(s.gex for s in strike_list)
        gex_bn    = total_gex / 1e9
        total_vex = sum(s.vex for s in strike_list)
        vex_bn    = total_vex / 1e9
        total_cex = sum(s.cex for s in strike_list)
        cex_bn    = total_cex / 1e9
        vex_regime = ('positive_vanna' if total_vex > 0.05e9
                      else 'negative_vanna' if total_vex < -0.05e9
                      else 'neutral')
        cex_regime = ('positive_charm' if total_cex > 0.01e9
                      else 'negative_charm' if total_cex < -0.01e9
                      else 'neutral')
        # Combined dealer pressure: weighted sum of all three flows
        # GEX(0.5) + VEX(0.3) + CEX(0.2) normalized to billion-scale
        dealer_pressure = round(0.5 * gex_bn + 0.3 * vex_bn + 0.2 * cex_bn, 3)

        # Gamma flip: strike where cumulative GEX crosses zero
        gamma_flip = self._find_gamma_flip(strike_list)

        # Gamma walls: strikes with highest absolute GEX
        pos_strikes = [s for s in strike_list if s.gex > 0]
        neg_strikes = [s for s in strike_list if s.gex < 0]
        positive_wall = max(pos_strikes, key=lambda x: x.gex).strike if pos_strikes else None
        negative_wall = min(neg_strikes, key=lambda x: x.gex).strike if neg_strikes else None

        # Nearest wall to spot
        walls = [w for w in [positive_wall, negative_wall] if w is not None]
        nearest_wall = min(walls, key=lambda w: abs(w - spot)) if walls else None
        wall_dist_pct = abs(nearest_wall - spot) / spot * 100 if nearest_wall else 0.0

        # Regime
        if total_gex > 0.1e9:
            regime = 'positive_gamma'
        elif total_gex < -0.1e9:
            regime = 'negative_gamma'
        else:
            regime = 'neutral'

        # Squeeze score (0–100)
        squeeze_score = self._compute_squeeze_score(
            total_gex, total_vex, total_cex, spot, gamma_flip, nearest_wall, momentum_z
        )
        squeeze_active = squeeze_score > SQUEEZE_SCORE_THRESHOLD

        return GammaProfile(
            symbol=symbol, spot=spot, total_gex=total_gex,
            gex_per_billion=round(gex_bn, 3),
            total_vex=total_vex,
            vex_per_billion=round(vex_bn, 3),
            vex_regime=vex_regime,
            total_cex=total_cex,
            cex_per_billion=round(cex_bn, 3),
            cex_regime=cex_regime,
            dealer_pressure=dealer_pressure,
            gamma_flip=gamma_flip, positive_wall=positive_wall,
            negative_wall=negative_wall, nearest_wall=nearest_wall,
            wall_distance_pct=round(wall_dist_pct, 2),
            regime=regime, squeeze_score=round(squeeze_score, 1),
            squeeze_active=squeeze_active, momentum_z=round(momentum_z, 2),
            strikes=strike_list,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _find_gamma_flip(self, strike_list: List[StrikeGEX]) -> Optional[float]:
        """Find strike where cumulative GEX changes sign (above → below spot)."""
        if not strike_list:
            return None
        cum = 0.0
        prev_strike = None
        for s in strike_list:
            prev_cum = cum
            cum += s.gex
            if prev_strike and ((prev_cum > 0) != (cum > 0)):
                # Linear interpolation between prev and current strike
                if (cum - prev_cum) != 0:
                    frac = -prev_cum / (cum - prev_cum)
                    return round(prev_strike + frac * (s.strike - prev_strike), 1)
            prev_strike = s.strike
        return None

    def _compute_squeeze_score(self, total_gex: float, total_vex: float,
                                total_cex: float, spot: float,
                                gamma_flip: Optional[float],
                                nearest_wall: Optional[float],
                                momentum_z: float) -> float:
        """
        Combined GEX+VEX squeeze score (0–100).
        Components:
          - GEX negativity    (30 pts): negative GEX = dealers amplify moves
          - VEX positivity    (20 pts): positive VEX = IV drop → dealers buy
          - GEX+VEX combo     (15 pts): negative GEX AND positive VEX = max squeeze
          - Flip proximity    (20 pts): how close is spot to gamma flip?
          - Momentum          (15 pts): price momentum z-score

        Case: neg_GEX + pos_VEX = maximum squeeze potential
        Case: pos_GEX + neg_VEX = maximum mean reversion
        """
        score = 0.0
        gex_bn = total_gex / 1e9
        vex_bn = total_vex / 1e9

        # 1. GEX component (30 pts)
        if gex_bn < 0:
            gex_pts = min(30.0, abs(gex_bn) * 15)
        else:
            gex_pts = max(0.0, 8 - gex_bn * 4)
        score += gex_pts

        # 2. VEX component (20 pts): positive VEX + falling IV = squeeze accelerator
        if vex_bn > 0:
            vex_pts = min(20.0, vex_bn * 10)
        else:
            vex_pts = 0.0
        score += vex_pts

        # 3. Combo bonus (15 pts): negative GEX AND positive VEX = feedback loop
        if gex_bn < 0 and vex_bn > 0:
            combo_pts = min(15.0, (abs(gex_bn) + vex_bn) * 5)
        else:
            combo_pts = 0.0
        score += combo_pts

        # 4. Flip proximity (20 pts)
        if gamma_flip and spot:
            flip_dist_pct = abs(gamma_flip - spot) / spot * 100
            flip_pts = max(0.0, 20 * (1 - flip_dist_pct / 3))
        else:
            flip_pts = 0.0
        score += flip_pts

        # 5. CEX (10 pts): large absolute CEX near expiry = charm squeeze
        cex_bn = total_cex / 1e9
        cex_pts = min(10.0, abs(cex_bn) * 5)
        score += cex_pts

        # 6. Momentum (10 pts)
        mom_pts = min(10.0, max(0.0, abs(momentum_z) * 4))
        score += mom_pts

        return min(100.0, score)

    def _empty_profile(self, symbol: str, spot: float) -> GammaProfile:
        return GammaProfile(
            symbol=symbol, spot=spot, total_gex=0.0, gex_per_billion=0.0,
            gamma_flip=None, positive_wall=None, negative_wall=None,
            nearest_wall=None, wall_distance_pct=0.0,
            regime='neutral', squeeze_score=0.0, squeeze_active=False,
            momentum_z=0.0, timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _persist(self, symbol: str, profile: GammaProfile):
        """Persist latest GEX profiles to disk for dashboard + debugging."""
        try:
            existing = {}
            if CACHE_FILE.exists():
                existing = json.loads(CACHE_FILE.read_text())
            existing[symbol] = {
                'gex_bn':         profile.gex_per_billion,
                'vex_bn':         profile.vex_per_billion,
                'cex_bn':         profile.cex_per_billion,
                'dealer_pressure':profile.dealer_pressure,
                'regime':         profile.regime,
                'vex_regime':     profile.vex_regime,
                'cex_regime':     profile.cex_regime,
                'gamma_flip':     profile.gamma_flip,
                'nearest_wall':   profile.nearest_wall,
                'wall_dist_pct':  profile.wall_distance_pct,
                'squeeze_score':  profile.squeeze_score,
                'squeeze_active': profile.squeeze_active,
                'timestamp':      profile.timestamp,
                # Top 20 strikes for charting
                'top_strikes': [
                    {'strike': s.strike, 'gex': round(s.gex / 1e6, 2),
                     'call_oi': s.call_oi, 'put_oi': s.put_oi}
                    for s in sorted(profile.strikes, key=lambda x: abs(x.gex), reverse=True)[:20]
                ],
            }
            tmp = CACHE_FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(existing, indent=2))
            tmp.rename(CACHE_FILE)
        except Exception as e:
            logger.debug('[GEX] persist failed: %s', e)


# ── Telegram alerts ──────────────────────────────────────────────────────────

def alert_squeeze(profile: GammaProfile):
    """Send Telegram alert when squeeze is detected."""
    try:
        from options.telegram_alerts import _send
        regime_emoji = '🔴' if profile.regime == 'negative_gamma' else '🟢'
        lines = [
            f"⚡ <b>Gamma Squeeze Alert — ${profile.symbol}</b>",
            "",
            f"{regime_emoji} GEX: <b>{profile.gex_per_billion:+.2f}B</b>  ({profile.regime.replace('_',' ')})",
            f"📍 Spot: ${profile.spot:.2f}",
        ]
        vex_emoji = '🟢' if profile.vex_per_billion > 0 else '🔴'
        lines.append(f"{vex_emoji} VEX: <b>{profile.vex_per_billion:+.2f}B</b>  ({profile.vex_regime.replace('_',' ')})")
        if profile.gamma_flip:
            dist = profile.gamma_flip - profile.spot
            lines.append(f"🎯 Gamma Flip: ${profile.gamma_flip:.1f}  ({dist:+.1f} from spot)")
        if profile.nearest_wall:
            lines.append(f"🧱 Nearest Wall: ${profile.nearest_wall:.1f}  ({profile.wall_distance_pct:.1f}% away)")
        # Regime interpretation
        if profile.regime == 'negative_gamma' and profile.vex_regime == 'positive_vanna':
            interp = "⚡ MAX SQUEEZE: neg GEX + pos VEX = feedback loop active"
        elif profile.regime == 'positive_gamma' and profile.vex_regime == 'negative_vanna':
            interp = "🛡 Max mean reversion: pos GEX + neg VEX = moves dampened"
        elif profile.regime == 'negative_gamma':
            interp = "📈 Gamma squeeze: momentum calls/puts"
        else:
            interp = "〰️ Stable: positive gamma environment"
        lines += [
            "",
            f"📊 Combined Squeeze Score: <b>{profile.squeeze_score:.0f}/100</b>",
            f"📈 Momentum: {profile.momentum_z:+.1f}σ",
            "",
            f"<i>{interp}</i>",
        ]
        _send('\n'.join(lines))
    except Exception as e:
        logger.warning('[GEX] Telegram alert failed: %s', e)


# ── Convenience function ─────────────────────────────────────────────────────

_scanner = None

def get_scanner() -> GammaScanner:
    global _scanner
    if _scanner is None:
        _scanner = GammaScanner()
    return _scanner


def scan_and_alert(symbol: str, spot: float, iv: float = 0.30,
                   momentum_z: float = 0.0) -> Optional[GammaProfile]:
    """Top-level helper: scan + alert if squeeze detected."""
    scanner = get_scanner()
    profile = scanner.scan(symbol, spot, iv_fallback=iv, momentum_z=momentum_z)
    if profile and profile.squeeze_active:
        alert_squeeze(profile)
    return profile
