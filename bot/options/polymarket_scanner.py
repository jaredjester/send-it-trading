"""
Polymarket signal scanner for Options Trading.

Fetches finance/economics prediction markets, scores directional signals
for watchlist tickers and macro themes, sends Telegram alerts on:
  - High-conviction thresholds (>75%, >85%, >90%)
  - Rapid probability shifts (>8% in 24h)
  - Markets directly related to watchlist symbols
  - Macro markets (Fed, CPI, recession) that affect the whole portfolio

Output: data/polymarket_intel.json
"""
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

DATA_DIR   = Path(__file__).resolve().parent.parent.parent / 'data'
CACHE_FILE = DATA_DIR / 'polymarket_intel.json'
GAMMA_API  = 'https://gamma-api.polymarket.com'
CACHE_TTL  = 600   # 10 min — refresh each overnight cycle
TIMEOUT    = 15

# ── Watchlist symbol → keyword map ───────────────────────
# Whole-word / phrase-only keywords — must be surrounded by spaces or punctuation
import re as _re

def _wm(text, phrases):
    """Word-boundary match: phrase must not be a substring of a longer word."""
    for phrase in phrases:
        pattern = r'(?<![a-z])' + _re.escape(phrase) + r'(?![a-z])'
        if _re.search(pattern, text):
            return True
    return False

SYMBOL_KEYWORDS = {
    'NVDA':  ['nvidia', 'nvda', 'jensen huang', 'blackwell h100', 'h100', 'hopper gpu'],
    'TSLA':  ['tesla', 'elon musk ceo', 'cybertruck', 'robotaxi', 'tesla stock',
              'tesla earnings', 'tesla sales'],
    'AAPL':  ['apple inc', 'iphone', 'app store', 'tim cook', 'vision pro', 'apple earnings'],
    'MSFT':  ['microsoft', 'azure', 'openai deal', 'activision', 'copilot ai'],
    'AMZN':  ['amazon', 'aws cloud', 'andy jassy', 'amazon earnings', 'amazon stock'],
    'GOOGL': ['google search', 'alphabet inc', 'google ai', 'waymo', 'google antitrust',
              'google earnings', 'gemini ai'],
    'META':  ['meta platforms', 'facebook stock', 'instagram', 'mark zuckerberg ceo',
              'meta earnings', 'meta ai', 'threads app'],
    'COIN':  ['coinbase', 'coinbase stock'],
    'PLTR':  ['palantir', 'pltr'],
    'NFLX':  ['netflix', 'netflix stock', 'netflix earnings', 'netflix subscribers'],
    'AMD':   [' amd ', 'lisa su', 'advanced micro devices', 'amd earnings', 'amd stock'],
    'UBER':  ['uber stock', 'uber earnings', 'uber technologies', 'uber app',
              'uber autonomous', 'uber ipo'],
    'BABA':  ['alibaba', 'baba stock', 'jack ma', 'alibaba earnings'],
    'SPY':   ['s&p 500', 'sp 500', 'sp500', 'stock market crash', 'dow jones',
              'us stock market', 'equity market', 'stock market rally', 'wall street'],
    'QQQ':   ['nasdaq 100', 'nasdaq composite', 'tech sector', 'tech stocks crash'],
}

# ── Macro keyword → directional impact ───────────────────
# (keyword, bullish_if_yes, label)
MACRO_MARKETS = [
    # Fed / Rates
    ('fed rate cut',        True,  'Fed Rate Cut'),
    ('fomc cut',            True,  'FOMC Rate Cut'),
    ('interest rate cut',   True,  'Interest Rate Cut'),
    ('rate hike',           False, 'Rate Hike'),
    ('fed pause',           True,  'Fed Pause'),
    ('powell resign',       False, 'Powell Uncertainty'),
    # Inflation
    ('cpi below',           True,  'CPI Miss (Soft)'),
    ('inflation below',     True,  'Inflation Cooling'),
    ('inflation above',     False, 'Inflation Hot'),
    ('core pce',            True,  'PCE Soft'),
    # Macro risk
    ('recession',           False, 'Recession Risk'),
    ('us recession',        False, 'US Recession'),
    ('debt ceiling',        False, 'Debt Ceiling Crisis'),
    ('government shutdown', False, 'Gov Shutdown'),
    ('trade war',           False, 'Trade War'),
    ('tariff',              False, 'Tariff Risk'),
    # Jobs
    ('jobs report',         True,  'Jobs Report Beat'),
    ('unemployment below',  True,  'Unemployment Low'),
    ('unemployment above',  False, 'Unemployment High'),
    # Crypto / risk-on
    ('bitcoin',             True,  'Bitcoin Signal'),
    ('btc',                 True,  'BTC Signal'),
    ('crypto etf',          True,  'Crypto ETF Approval'),
    # IPO / M&A
    ('ipo',                 True,  'IPO Activity'),
    ('acquisition',         True,  'M&A Activity'),
    ('merger',              True,  'Merger Activity'),
    # Market structure
    ('market crash',        False, 'Market Crash Risk'),
    ('stock market',        True,  'Stock Market Up'),
    ('sp 500',              True,  'S&P Signal'),
    ('nasdaq',              True,  'Nasdaq Signal'),
]

# Alert thresholds
THRESHOLD_STRONG  = 0.80   # yes_prob above this → strong signal
THRESHOLD_ALERT   = 0.70   # yes_prob above this → alert
THRESHOLD_LOW     = 0.20   # yes_prob below this → strong NO signal
MOMENTUM_ALERT    = 0.08   # 24h price change above this → momentum alert
MIN_VOLUME        = 5000   # minimum market volume to be worth watching
MIN_LIQUIDITY     = 1000   # minimum liquidity


# ── Fetch ─────────────────────────────────────────────────
def _fetch_markets(pages: int = 3) -> List[dict]:
    """Fetch top-volume open markets from Gamma API."""
    markets = []
    for page in range(pages):
        try:
            r = requests.get(f'{GAMMA_API}/markets', params={
                'limit':      100,
                'closed':     'false',
                'order':      'volume',
                'ascending':  'false',
                'offset':     page * 100,
            }, timeout=TIMEOUT)
            if r.ok:
                markets.extend(r.json())
            else:
                log.warning(f'[POLY] Page {page}: HTTP {r.status_code}')
        except Exception as e:
            log.warning(f'[POLY] Fetch page {page}: {e}')
    log.info(f'[POLY] Fetched {len(markets)} markets total')
    return markets


def _parse_price(market: dict) -> Tuple[float, float]:
    """Return (yes_prob, no_prob) from outcomePrices field."""
    try:
        raw = market.get('outcomePrices', '["0.5","0.5"]')
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes_p = float(prices[0]) if prices else 0.5
        no_p  = float(prices[1]) if len(prices) > 1 else 1 - yes_p
        return round(yes_p, 4), round(no_p, 4)
    except Exception:
        return 0.5, 0.5


# ── Classify markets ──────────────────────────────────────
# Noise patterns — skip these even if keyword matches
_NOISE = [
    'oscar', 'emmy', 'grammy', 'golden globe', 'academy award', 'box office',
    'gubernatorial', 'senator', 'prime minister', 'president of', 'election',
    'tweet', 'tweets', 'post count', 'follower', 'instagram follower',
    'nba', 'nfl', 'mlb', 'nhl', 'super bowl', 'world cup', 'champion',
    'celebrity', 'dating', 'baby', 'marriage', 'divorce',
    'movie', 'film', 'album', 'song', 'concert', 'tour',
]

def _match_symbol(question: str, desc: str = '') -> Optional[str]:
    """Return matching watchlist symbol or None."""
    text = (question + ' ' + desc).lower()
    # Reject noise
    if any(n in text for n in _NOISE):
        return None
    for sym, kws in SYMBOL_KEYWORDS.items():
        if _wm(text, kws):
            return sym
    return None


def _match_macro(question: str) -> Optional[Tuple[bool, str]]:
    """Return (bullish_if_yes, label) if market matches a macro theme."""
    q = question.lower()
    # Reject noise first
    if any(n in q for n in _NOISE):
        return None
    for kw, bullish, label in MACRO_MARKETS:
        if kw in q:
            return bullish, label
    return None


def _signal_from_market(market: dict) -> Optional[dict]:
    """
    Build a signal dict from a market.
    Returns None if below volume/liquidity threshold or not relevant.
    """
    vol = float(market.get('volumeNum') or market.get('volume') or 0)
    liq = float(market.get('liquidityNum') or market.get('liquidity') or 0)
    if vol < MIN_VOLUME:
        return None

    question   = market.get('question', '')
    desc       = market.get('description', '') or ''
    yes_p, no_p = _parse_price(market)
    d1_change  = float(market.get('oneDayPriceChange')  or 0)
    h1_change  = float(market.get('oneHourPriceChange') or 0)
    wk_change  = float(market.get('oneWeekPriceChange') or 0)

    # Is it relevant?
    symbol    = _match_symbol(question, desc)
    macro     = _match_macro(question)
    if not symbol and not macro:
        return None

    # Directional score: positive = bullish, negative = bearish
    if macro:
        bullish_if_yes, label = macro
        # If yes_prob is high and market is "bullish if yes" → bullish signal
        score = (yes_p - 0.5) * (1 if bullish_if_yes else -1)
    else:
        # Symbol-specific — high yes = bullish by default
        score = yes_p - 0.5
        label = symbol

    # Momentum signal: rapidly changing market = uncertainty
    momentum = abs(d1_change)

    # Conviction: how extreme is the probability
    conviction = 'strong'  if abs(yes_p - 0.5) > 0.30 else \
                 'moderate' if abs(yes_p - 0.5) > 0.15 else 'weak'

    return {
        'question':   question,
        'symbol':     symbol,
        'macro':      macro[1] if macro else None,
        'bullish_if_yes': macro[0] if macro else True,
        'yes_prob':   yes_p,
        'no_prob':    no_p,
        'score':      round(score, 4),
        'conviction': conviction,
        'direction':  'bullish' if score > 0.05 else ('bearish' if score < -0.05 else 'neutral'),
        'd1_change':  round(d1_change, 4),
        'h1_change':  round(h1_change, 4),
        'wk_change':  round(wk_change, 4),
        'momentum':   round(momentum, 4),
        'volume':     round(vol, 0),
        'liquidity':  round(liq, 0),
        'end_date':   market.get('endDateIso', ''),
        'market_id':  market.get('id', ''),
        'url':        f"https://polymarket.com/event/{market.get('slug','')}"
    }


# ── Main scanner ──────────────────────────────────────────
def run_polymarket_scan(force: bool = False) -> dict:
    """
    Full Polymarket scan. Returns intel dict, writes to CACHE_FILE.
    """
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            age    = time.time() - cached.get('_ts', 0)
            if age < CACHE_TTL:
                log.info(f'[POLY] Cache hit ({age:.0f}s old)')
                return cached
        except Exception:
            pass

    log.info('[POLY] Starting Polymarket scan...')
    markets  = _fetch_markets(pages=3)
    signals  = []
    by_symbol: Dict[str, list] = {}
    macro_signals: list = []

    for m in markets:
        sig = _signal_from_market(m)
        if not sig:
            continue
        signals.append(sig)
        if sig['symbol']:
            by_symbol.setdefault(sig['symbol'], []).append(sig)
        if sig['macro']:
            macro_signals.append(sig)

    # Sort each symbol's signals by volume
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda x: x['volume'], reverse=True)

    macro_signals.sort(key=lambda x: x['volume'], reverse=True)

    # Top movers (biggest 24h shifts)
    movers = sorted(signals, key=lambda x: x['momentum'], reverse=True)[:10]

    # High conviction markets
    high_conviction = [s for s in signals if s['conviction'] == 'strong']
    high_conviction.sort(key=lambda x: x['volume'], reverse=True)

    intel = {
        '_ts':            time.time(),
        'scanned_at':     datetime.now().isoformat(),
        'total_markets':  len(markets),
        'relevant':       len(signals),
        'by_symbol':      by_symbol,
        'macro':          macro_signals[:20],
        'movers':         movers,
        'high_conviction':high_conviction[:15],
    }

    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(intel, indent=2))

    log.info(f'[POLY] Scan complete: {len(signals)} relevant markets | '
             f'{len(macro_signals)} macro | {len(high_conviction)} high-conviction')
    return intel


# ── Telegram alerts ───────────────────────────────────────
def send_alerts(intel: dict):
    """Fire Telegram alerts for notable markets."""
    try:
        from options import telegram_alerts as tg
    except Exception:
        return

    import time as _t

    _SENT    = {}
    COOLDOWN = 3600 * 3   # 3h per market

    def _ok(mid):
        now = _t.time()
        if now - _SENT.get(mid, 0) < COOLDOWN:
            return False
        _SENT[mid] = now
        return True

    def _pct(v): return f'{v:.0%}'
    def _chg(v): return ('+' if v > 0 else '') + f'{v:.0%}'

    # 1. High-conviction macro alerts
    for sig in intel.get('macro', []):
        if sig['conviction'] != 'strong':
            continue
        if abs(sig['yes_prob'] - 0.5) < 0.30:
            continue
        if not _ok(sig['market_id']):
            continue

        emoji  = '🟢' if sig['direction'] == 'bullish' else '🔴'
        trend  = '📈' if sig['direction'] == 'bullish' else '📉'
        chg_s  = f' ({_chg(sig["d1_change"])} 24h)' if abs(sig['d1_change']) > 0.02 else ''
        lines  = [
            f"{emoji} <b>Polymarket Signal — {sig['macro']}</b>",
            f"",
            f"❓ {sig['question'][:100]}",
            f"",
            f"YES: <b>{_pct(sig['yes_prob'])}</b>{chg_s}  ·  Vol: ${sig['volume']:,.0f}",
            f"Signal: {trend} <b>{sig['direction'].upper()}</b> for equities",
            f"",
            f"<i>Polymarket · Options Trading</i>",
        ]
        tg._send('\n'.join(lines))
        log.info(f"[POLY] TG alert sent: {sig['macro']} ({sig['question'][:60]})")

    # 2. Symbol-specific high-conviction markets
    for sym, sigs in intel.get('by_symbol', {}).items():
        for sig in sigs[:2]:   # top 2 by volume per symbol
            if sig['conviction'] == 'weak':
                continue
            if sig['volume'] < 20000:
                continue
            if not _ok(sig['market_id']):
                continue

            emoji = '🟢' if sig['direction'] == 'bullish' else ('🔴' if sig['direction'] == 'bearish' else '⚪')
            chg_s = f' ({_chg(sig["d1_change"])} 24h)' if abs(sig['d1_change']) > 0.03 else ''
            lines = [
                f"{emoji} <b>Polymarket — #{sym}</b>",
                f"",
                f"❓ {sig['question'][:100]}",
                f"",
                f"YES: <b>{_pct(sig['yes_prob'])}</b>{chg_s}  ·  Vol: ${sig['volume']:,.0f}",
                f"Signal: <b>{sig['direction'].upper()}</b>",
                f"",
                f"<i>Polymarket · Options Trading</i>",
            ]
            tg._send('\n'.join(lines))
            log.info(f"[POLY] TG alert sent: {sym} ({sig['question'][:60]})")

    # 3. Momentum alerts — markets moving fast
    for sig in intel.get('movers', []):
        if sig['momentum'] < MOMENTUM_ALERT:
            continue
        if sig['volume'] < 30000:
            continue
        if not _ok(f"momentum:{sig['market_id']}"):
            continue

        sym_tag = f' #{sig["symbol"]}' if sig['symbol'] else ''
        chg_s   = _chg(sig['d1_change'])
        emoji   = '🔥' if sig['d1_change'] > 0 else '🌊'
        lines   = [
            f"{emoji} <b>Polymarket Moving Fast{sym_tag}</b>",
            f"",
            f"❓ {sig['question'][:100]}",
            f"",
            f"YES: <b>{_pct(sig['yes_prob'])}</b>  ·  24h: <b>{chg_s}</b>",
            f"Vol: ${sig['volume']:,.0f}",
            f"",
            f"<i>Polymarket · Options Trading</i>",
        ]
        tg._send('\n'.join(lines))
        log.info(f"[POLY] Momentum TG alert: {sig['question'][:60]}")


# ── Ledger path ──────────────────────────────────────────
LEDGER_FILE = DATA_DIR / 'poly_ledger.jsonl'
GAMMA_API_BASE = GAMMA_API   # alias


# ── Live signal lookup ────────────────────────────────────
def get_symbol_score(symbol: str) -> float:
    """
    Return current Polymarket directional score for a watchlist symbol.
    Positive = bullish (high YES on bullish event), negative = bearish.
    Range: approximately -0.5 to +0.5
    Returns 0.0 if no relevant market found.
    """
    try:
        if CACHE_FILE.exists():
            intel  = json.loads(CACHE_FILE.read_text())
            sigs   = intel.get('by_symbol', {}).get(symbol, [])
            if sigs:
                # Weighted average by volume
                total_vol = sum(s.get('volume', 0) for s in sigs)
                if total_vol > 0:
                    score = sum(s['score'] * s.get('volume', 0) for s in sigs) / total_vol
                    return round(score, 4)
                return round(sigs[0]['score'], 4)
    except Exception as e:
        log.debug(f'[POLY] get_symbol_score {symbol}: {e}')
    return 0.0


def get_macro_score() -> float:
    """
    Return aggregate macro Polymarket score (volume-weighted).
    Positive = macro bullish, negative = macro bearish.
    """
    try:
        if CACHE_FILE.exists():
            intel  = json.loads(CACHE_FILE.read_text())
            macro  = intel.get('macro', [])
            # Weight by volume, min $50k
            usable = [s for s in macro if s.get('volume', 0) >= 50000]
            if usable:
                total_vol = sum(s['volume'] for s in usable)
                score     = sum(s['score'] * s['volume'] for s in usable) / total_vol
                return round(score, 4)
    except Exception as e:
        log.debug(f'[POLY] get_macro_score: {e}')
    return 0.0


# ── Ledger: record signal at trade entry ──────────────────
def track_trade_bet(trade_id: str, symbol: str, trade_direction: str,
                    poly_score: float, markets: list = None):
    """
    Record Polymarket signal context at trade entry.
    Called when a trade is placed so we can evaluate later.

    trade_direction: 'call' (bullish bet) or 'put' (bearish bet)
    poly_score:      combined symbol + macro score at trade time
    markets:         list of specific markets that contributed to score
    """
    if not markets:
        try:
            if CACHE_FILE.exists():
                intel   = json.loads(CACHE_FILE.read_text())
                markets = intel.get('by_symbol', {}).get(symbol, [])[:3]
        except Exception:
            markets = []

    entry = {
        'trade_id':         trade_id,
        'symbol':           symbol,
        'trade_direction':  trade_direction,
        'poly_score':       poly_score,
        'entry_ts':         time.time(),
        'entry_dt':         datetime.now().isoformat(),
        'markets':          [
            {
                'market_id': m.get('market_id', ''),
                'question':  m.get('question', '')[:100],
                'yes_prob':  m.get('yes_prob', 0.5),
                'volume':    m.get('volume', 0),
                'direction': m.get('direction', 'neutral'),
            }
            for m in (markets or [])[:5]
        ],
        'resolved':         False,
        'outcome':          None,   # filled when trade closes
        'poly_resolved':    None,   # filled when Polymarket markets resolve
        'pnl':              None,
        'ic_alignment':     None,   # +1 = signal correct, -1 = signal wrong
        'brier_score':      None,   # 0=perfect, 1=worst
    }

    DATA_DIR.mkdir(exist_ok=True)
    with open(LEDGER_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    log.info(f'[POLY] Ledger: recorded trade {trade_id} | {symbol} poly_score={poly_score:.3f}')


# ── Close a trade entry in the ledger ─────────────────────
def record_trade_outcome(trade_id: str, pnl: float):
    """
    Update the ledger with trade P&L outcome.
    Called when a trade closes (from rl.close_trade).
    """
    if not LEDGER_FILE.exists():
        return
    try:
        lines = LEDGER_FILE.read_text().strip().split('\n')
        updated = []
        changed = False
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get('trade_id') == trade_id and not entry.get('resolved'):
                entry['pnl']         = pnl
                entry['outcome']     = 'win' if pnl > 0 else 'loss'
                entry['resolved']    = True
                # Compute IC alignment: did poly signal direction match outcome?
                score     = entry.get('poly_score', 0.0)
                trade_dir = 1.0 if entry.get('trade_direction') == 'call' else -1.0
                outcome   = 1.0 if pnl > 0 else -1.0
                if abs(score) > 0.02:  # only count if signal had conviction
                    entry['ic_alignment'] = 1.0 if (score * trade_dir * outcome) > 0 else -1.0
                changed = True
            updated.append(json.dumps(entry))
        if changed:
            LEDGER_FILE.write_text('\n'.join(updated) + '\n')
            log.info(f'[POLY] Ledger: outcome recorded for {trade_id} | pnl={pnl:+.2f}')
    except Exception as e:
        log.warning(f'[POLY] Ledger outcome update failed: {e}')


# ── Check resolved Polymarket markets ─────────────────────
def check_resolutions() -> dict:
    """
    For each open ledger entry with specific market IDs,
    check if those Polymarket markets have resolved.
    Compute Brier scores and IC for resolved entries.
    Returns summary dict.
    """
    if not LEDGER_FILE.exists():
        return {}

    try:
        lines   = LEDGER_FILE.read_text().strip().split('\n')
        entries = [json.loads(l) for l in lines if l.strip()]
    except Exception:
        return {}

    # Only process entries with trade outcomes but unresolved Poly markets
    pending = [e for e in entries
               if e.get('resolved') and e.get('poly_resolved') is None
               and e.get('markets')]

    if not pending:
        log.info('[POLY] No pending market resolutions to check')
        return {}

    log.info(f'[POLY] Checking {len(pending)} ledger entries for market resolution...')
    resolved_count = 0

    for entry in pending:
        for mkt in entry.get('markets', []):
            mid = mkt.get('market_id', '')
            if not mid:
                continue
            try:
                r = requests.get(f'{GAMMA_API_BASE}/markets/{mid}', timeout=10)
                if not r.ok:
                    continue
                m_data = r.json()
                if not m_data.get('closed', False):
                    continue
                # Market resolved — read final YES probability
                prices    = json.loads(m_data.get('outcomePrices', '["0.5","0.5"]'))
                final_yes = float(prices[0])  # 1.0 = YES won, 0.0 = NO won

                # Brier score for this market prediction
                pred_yes  = mkt.get('yes_prob', 0.5)
                brier     = (pred_yes - final_yes) ** 2
                mkt['resolved']   = True
                mkt['final_yes']  = final_yes
                mkt['brier']      = round(brier, 4)
                resolved_count   += 1

                log.info(f'[POLY] Resolved: {mkt["question"][:60]} | '
                         f'pred={pred_yes:.0%} → actual={final_yes:.0%} | '
                         f'Brier={brier:.3f}')
            except Exception as e:
                log.debug(f'[POLY] Resolution check {mid}: {e}')

        # If all markets resolved, update entry
        if all(m.get('resolved') for m in entry.get('markets', [])):
            brierscores = [m['brier'] for m in entry['markets'] if 'brier' in m]
            entry['poly_resolved'] = True
            entry['brier_score']   = round(sum(brierscores) / len(brierscores), 4) if brierscores else None
            log.info(f'[POLY] Ledger entry {entry["trade_id"]} fully resolved | '
                     f'avg_brier={entry.get("brier_score")}')

    # Write back
    try:
        LEDGER_FILE.write_text('\n'.join(json.dumps(e) for e in entries) + '\n')
    except Exception as e:
        log.warning(f'[POLY] Ledger write-back failed: {e}')

    return _compute_calibration(entries)


# ── Calibration summary ────────────────────────────────────
def _compute_calibration(entries: list) -> dict:
    """Compute overall Polymarket signal calibration from ledger."""
    resolved_trades = [e for e in entries
                       if e.get('resolved') and e.get('ic_alignment') is not None]

    brier_entries   = [e for e in entries
                       if e.get('brier_score') is not None]

    if not resolved_trades:
        return {'ic': 0.0, 'n_trades': 0, 'avg_brier': None, 'directional_acc': 0.0}

    alignments = [e['ic_alignment'] for e in resolved_trades]
    ic          = sum(alignments) / len(alignments)
    dir_acc     = sum(1 for a in alignments if a > 0) / len(alignments)

    avg_brier   = None
    if brier_entries:
        avg_brier = round(sum(e['brier_score'] for e in brier_entries) / len(brier_entries), 4)

    result = {
        'ic':             round(ic, 4),
        'n_trades':       len(resolved_trades),
        'directional_acc':round(dir_acc, 4),
        'avg_brier':      avg_brier,
        'n_brier':        len(brier_entries),
    }
    log.info(f'[POLY] Calibration: IC={ic:.4f} dir_acc={dir_acc:.1%} '
             f'Brier={avg_brier} n={len(resolved_trades)}')
    return result


def get_calibration_summary() -> dict:
    """Quick calibration read from ledger without fetching new resolutions."""
    if not LEDGER_FILE.exists():
        return {'ic': 0.0, 'n_trades': 0, 'directional_acc': 0.0, 'avg_brier': None}
    try:
        entries = [json.loads(l) for l in LEDGER_FILE.read_text().strip().split('\n') if l.strip()]
        return _compute_calibration(entries)
    except Exception:
        return {'ic': 0.0, 'n_trades': 0, 'directional_acc': 0.0, 'avg_brier': None}
