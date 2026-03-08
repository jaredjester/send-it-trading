"""
News scanner — Alpaca News API (real headlines) + yfinance fallback.
FinBERT-scores article headlines/summaries per symbol.
Outputs to data/news_intel.json.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import requests
try:
    from options import telegram_alerts as _tg
except Exception:
    _tg = None

log = logging.getLogger(__name__)

DATA_DIR   = Path('__ABSOLUTE_PATH_NEEDS_FIXING__')
INTEL_FILE = DATA_DIR / 'news_intel.json'
CACHE_TTL  = 600   # 10 min

# ── Load env ────────────────────────────────────────────
def _load_env():
    env_file = Path('__ABSOLUTE_PATH_NEEDS_FIXING__')
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
API_KEY    = os.environ.get('ALPACA_API_LIVE_KEY') or os.environ.get('ALPACA_API_KEY_ID', '')
API_SECRET = os.environ.get('ALPACA_API_SECRET')   or os.environ.get('APCA_API_SECRET_KEY', '')
DATA_URL   = 'https://data.alpaca.markets'


# ── VIX ─────────────────────────────────────────────────
def _fetch_vix() -> dict:
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX')
        h   = vix.history(period='2d', interval='1d')
        if h.empty:
            return {'level': None, 'regime': 'unknown', 'iv_multiplier': 1.0}
        level = round(float(h['Close'].iloc[-1]), 2)
        if level < 15:
            regime, mult = 'calm',     0.85
        elif level < 20:
            regime, mult = 'normal',   1.0
        elif level < 25:
            regime, mult = 'elevated', 1.15
        elif level < 35:
            regime, mult = 'fear',     1.30
        else:
            regime, mult = 'panic',    1.50
        return {'level': level, 'regime': regime, 'iv_multiplier': mult}
    except Exception as e:
        log.warning(f"VIX fetch: {e}")
        return {'level': None, 'regime': 'unknown', 'iv_multiplier': 1.0}


# ── Alpaca News ──────────────────────────────────────────
def _fetch_alpaca_news(symbol: str, limit: int = 8) -> List[dict]:
    """Fetch news articles via Alpaca News API (returns headlines + summaries)."""
    try:
        url = f"{DATA_URL}/v1beta1/news"
        params = {
            'symbols': symbol,
            'limit':   limit,
            'sort':    'desc',
        }
        headers = {
            'APCA-API-KEY-ID':     API_KEY,
            'APCA-API-SECRET-KEY': API_SECRET,
        }
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json().get('news', [])
        log.debug(f"Alpaca news {symbol}: HTTP {r.status_code}")
        return []
    except Exception as e:
        log.debug(f"Alpaca news {symbol}: {e}")
        return []


def _fetch_yf_news(symbol: str) -> List[dict]:
    """Fallback: yfinance news (headlines only, often empty body)."""
    try:
        import yfinance as yf
        t    = yf.Ticker(symbol)
        news = t.news or []
        out  = []
        for a in news[:8]:
            ct = a.get('content', {})
            headline = ct.get('title') or a.get('title', '')
            summary  = ct.get('summary') or a.get('summary', '')
            if headline:
                out.append({'headline': headline, 'summary': summary, 'source': 'yfinance'})
        return out
    except Exception as e:
        log.debug(f"yfinance news {symbol}: {e}")
        return []


def _finbert_score_articles(articles: List[dict]) -> dict:
    """Score articles with FinBERT, return aggregated result."""
    try:
        from engine.data_sources.finbert_sentiment import score_text, aggregate_sentiment

        scored = []
        for a in articles:
            text = a.get('headline', '') or a.get('title', '')
            summ = a.get('summary', '')
            if summ:
                text = text + '. ' + summ[:200]
            text = text.strip()
            if not text:
                continue
            res = score_text(text)
            if res:
                scored.append({
                    'headline': a.get('headline', a.get('title', '')),
                    'label':    res.get('label', 'neutral'),
                    'score':    round(res.get('score', 0.0), 3),
                    'source':   a.get('source', 'alpaca'),
                })

        if not scored:
            return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0, 'articles': []}

        # Aggregate
        pos = [s['score'] for s in scored if s['label'] == 'positive']
        neg = [s['score'] for s in scored if s['label'] == 'negative']
        neu = [s for s in scored if s['label'] == 'neutral']

        net_score  = (sum(pos) - sum(neg)) / len(scored)
        confidence = min(1.0, len(scored) / 5.0)
        label      = 'positive' if net_score > 0.05 else ('negative' if net_score < -0.05 else 'neutral')

        return {
            'score':      round(net_score, 3),
            'label':      label,
            'confidence': round(confidence, 2),
            'n':          len(scored),
            'articles':   scored[:5],
        }
    except Exception as e:
        log.warning(f"FinBERT news scoring: {e}")
        return {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0, 'articles': []}


# ── Pre-market price ─────────────────────────────────────
def _fetch_premarket(symbol: str) -> dict:
    try:
        url = f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest"
        headers = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': API_SECRET}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            q = r.json().get('quote', {})
            return {'bid': q.get('bp'), 'ask': q.get('ap'), 'source': 'alpaca'}
    except Exception:
        pass

    try:
        import yfinance as yf
        t   = yf.Ticker(symbol)
        inf = t.fast_info
        return {
            'last_price':      getattr(inf, 'last_price', None),
            'prev_close':      getattr(inf, 'previous_close', None),
            'extended_price':  getattr(inf, 'last_price', None),
            'pre_market':      getattr(inf, 'pre_market_price', None),
            'post_market':     getattr(inf, 'post_market_price', None),
            'source':          'yfinance',
            'symbol':          symbol,
        }
    except Exception as e:
        log.debug(f"premarket {symbol}: {e}")
        return {}


# ── Main scan ────────────────────────────────────────────
def run_news_scan(symbols: List[str], force: bool = False) -> dict:
    """
    Full overnight news + VIX + pre-market scan.
    Returns intel dict and writes to INTEL_FILE.
    """
    # Cache check (short TTL — refresh every cycle)
    if not force and INTEL_FILE.exists():
        try:
            cached = json.loads(INTEL_FILE.read_text())
            age    = time.time() - cached.get('_ts', 0)
            if age < CACHE_TTL:
                log.info(f"[NEWS] Cache hit ({age:.0f}s old)")
                return cached
        except Exception:
            pass

    log.info(f"[NEWS] Starting scan — {len(symbols)} symbols")

    # VIX
    vix = _fetch_vix()
    log.info(f"[NEWS] VIX={vix['level']} regime={vix['regime']} iv_mult={vix['iv_multiplier']}")

    sym_data     = {}
    market_proxy = None

    for sym in symbols:
        log.info(f"[NEWS] Scanning {sym}...")
        try:
            # Pre-market price
            pm = _fetch_premarket(sym)
            lp = pm.get('extended_price') or pm.get('last_price')
            pc = pm.get('prev_close')
            gap_pct = round(((lp - pc) / pc) * 100, 2) if lp and pc and pc != 0 else 0.0

            if sym == 'SPY':
                market_proxy = gap_pct

            # News — try Alpaca first, fall back to yfinance
            articles = _fetch_alpaca_news(sym, limit=8)
            if articles:
                # Normalise Alpaca format
                normalized = [{'headline': a.get('headline', ''), 'summary': a.get('summary', ''),
                               'source': 'alpaca', 'url': a.get('url', '')} for a in articles]
                log.info(f"[NEWS] {sym}: {len(normalized)} Alpaca articles")
            else:
                normalized = _fetch_yf_news(sym)
                log.info(f"[NEWS] {sym}: {len(normalized)} yfinance articles (fallback)")

            # FinBERT score
            news_result = _finbert_score_articles(normalized)
            log.info(f"[NEWS] {sym}: sentiment={news_result['label']} "
                     f"score={news_result['score']:.3f} n={news_result['n']}")

            if gap_pct > 2.0:
                log.warning(f"[NEWS] OVERNIGHT MOVER {sym}: +{gap_pct:.2f}% pre-market gap UP")
            elif gap_pct < -2.0:
                log.warning(f"[NEWS] OVERNIGHT MOVER {sym}: {gap_pct:.2f}% pre-market gap DOWN")

            # Combined directional score
            gap_dir     = 1.0 if gap_pct > 0 else (-1.0 if gap_pct < 0 else 0.0)
            combined    = round(news_result['score'] * 0.7 + (gap_pct / 10.0) * 0.3, 3)
            direction   = 'bullish' if combined > 0.05 else ('bearish' if combined < -0.05 else 'neutral')

            # Telegram alert for extreme news
            try:
                if _tg and abs(news_result['score']) >= 0.45 and news_result['n'] > 0:
                    _tg.alert_news(sym, news_result['articles'], combined, direction)
            except Exception:
                pass

            sym_data[sym] = {
                'pre_market':     pm,
                'gap_pct':        gap_pct,
                'news':           news_result,
                'combined_score': combined,
                'direction':      direction,
                'n_articles':     news_result['n'],
            }

        except Exception as e:
            log.error(f"[NEWS] {sym} failed: {e}")
            sym_data[sym] = {
                'pre_market': {}, 'gap_pct': 0.0,
                'news': {'score': 0.0, 'label': 'neutral', 'confidence': 0.0, 'n': 0, 'articles': []},
                'combined_score': 0.0, 'direction': 'neutral', 'n_articles': 0,
            }

    intel = {
        '_ts':          time.time(),
        'scanned_at':   datetime.now().isoformat(),
        'vix':          vix,
        'market_proxy': market_proxy,
        'symbols':      sym_data,
    }

    # Telegram: overnight movers
    try:
        if _tg:
            movers = [
                {'symbol': s, 'gap_pct': d['gap_pct'],
                 'direction': d['direction'],
                 'news_score': d['news']['score']}
                for s, d in sym_data.items() if abs(d.get('gap_pct', 0)) >= 2.0
            ]
            if movers:
                _tg.alert_overnight_movers(movers)
    except Exception:
        pass

    DATA_DIR.mkdir(exist_ok=True)
    INTEL_FILE.write_text(json.dumps(intel, indent=2))
    log.info(f"[NEWS] Scan complete. VIX={vix['level']} market_proxy={market_proxy}")
    return intel


# ── Compat alias ────────────────────────────────────────
def get_vix():
    """Convenience wrapper — returns just the VIX dict."""
    return _fetch_vix()
