"""
Insider activity scanner — fetches Form 4 filings via yfinance,
scores with FinBERT, caches to data/insider_intel.json.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)
try:
    from options_v1 import telegram_alerts as _tg
except Exception:
    _tg = None

DATA_DIR  = Path('__ABSOLUTE_PATH_NEEDS_FIXING__')
CACHE_FILE = DATA_DIR / 'insider_intel.json'
CACHE_TTL  = 3600 * 6   # 6 hours

# ──────────────────────────────────────────────
def _fetch_yf_insider(symbol: str) -> dict:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        df = t.insider_transactions
        if df is None or df.empty:
            return {'buys': 0, 'sells': 0, 'net_shares': 0, 'transactions': []}

        cutoff = datetime.now() - timedelta(days=30)
        buys = sells = 0
        net_shares   = 0
        txns         = []

        for idx, row in df.iterrows():
            try:
                # Date filter — index may be datetime or string
                if hasattr(idx, 'date'):
                    if idx.replace(tzinfo=None) < cutoff:
                        continue
                shares   = int(row.get('Shares', 0) or 0)
                txn_type = str(row.get('Transaction', '') or '')
                insider  = str(row.get('Insider', row.get('insider', ''))  or '')
                title    = str(row.get('Title', '')    or '')
                value    = float(row.get('Value', 0)   or 0)

                text_col = str(row.get('Text', '') or '')
                combined_text = (txn_type + ' ' + text_col).upper()
                is_b = any(w in combined_text for w in ['BUY', 'PURCH', 'ACQUI', 'GRANT', 'AWARD'])
                is_s = any(w in combined_text for w in ['SELL', 'SALE', 'DISPOS'])

                if is_b:
                    buys       += 1
                    net_shares += shares
                elif is_s:
                    sells      += 1
                    net_shares -= shares

                txns.append({
                    'insider':   insider,
                    'title':     title,
                    'type':      txn_type,
                    'shares':    shares,
                    'value':     round(value, 2),
                    'direction': 'buy' if is_b else ('sell' if is_s else 'other'),
                })
            except Exception:
                pass

        return {'buys': buys, 'sells': sells, 'net_shares': net_shares, 'transactions': txns[:6]}
    except Exception as e:
        log.debug(f"yfinance insider {symbol}: {e}")
        return {'buys': 0, 'sells': 0, 'net_shares': 0, 'transactions': []}


def _activity_score(raw: dict) -> tuple:
    """Return (score −1..1, label, confidence)."""
    b, s = raw.get('buys', 0), raw.get('sells', 0)
    total = b + s
    if total == 0:
        return 0.0, 'neutral', 0.0
    score      = (b - s) / total
    confidence = min(1.0, total / 5.0)
    label      = 'bullish' if score > 0.25 else ('bearish' if score < -0.25 else 'neutral')
    return round(score, 3), label, round(confidence, 2)


def _finbert_txn_score(txns: list) -> float:
    """Run FinBERT on transaction descriptions; return signed score."""
    try:
        from options_v1.finbert_sentiment import score_text
        texts = [f"{t['insider']} {t['type']} {t['shares']} shares of stock"
                 for t in txns if t.get('insider')]
        if not texts:
            return 0.0
        combined = '. '.join(texts[:3])
        res = score_text(combined)
        raw_score = res.get('score', 0.0)
        return round(raw_score if res.get('label') == 'positive' else -raw_score, 3)
    except Exception as e:
        log.debug(f"FinBERT insider: {e}")
        return 0.0


# ──────────────────────────────────────────────
def run_insider_scan(symbols: List[str], force: bool = False) -> Dict[str, dict]:
    """Scan insider activity for all symbols. Returns dict keyed by symbol."""
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            age    = time.time() - cached.get('_ts', 0)
            if age < CACHE_TTL:
                log.info(f"[INSIDER] Cache hit ({age/3600:.1f}h old)")
                return cached.get('data', {})
        except Exception:
            pass

    log.info(f"[INSIDER] Scanning {len(symbols)} symbols for insider activity...")
    results = {}

    for sym in symbols:
        try:
            raw              = _fetch_yf_insider(sym)
            score, label, cf = _activity_score(raw)
            fb_score         = _finbert_txn_score(raw.get('transactions', []))

            # Combined score: 70% activity ratio + 30% FinBERT
            combined = round(score * 0.70 + fb_score * 0.30, 3)

            results[sym] = {
                'buys':          raw['buys'],
                'sells':         raw['sells'],
                'net_shares':    raw['net_shares'],
                'score':         combined,
                'activity_score':score,
                'finbert_score': fb_score,
                'label':         label,
                'confidence':    cf,
                'transactions':  raw['transactions'],
                'scanned_at':    datetime.now().isoformat(),
            }
            log.info(
                f"[INSIDER] {sym}: {label} score={combined:.3f} "
                f"(buys={raw['buys']}, sells={raw['sells']}, fb={fb_score:.3f})"
            )
            # Telegram alert for extreme insider activity
            try:
                if _tg:
                    _tg.alert_insider(sym, results[sym])
            except Exception:
                pass
        except Exception as e:
            log.warning(f"[INSIDER] {sym} failed: {e}")
            results[sym] = {
                'buys': 0, 'sells': 0, 'net_shares': 0,
                'score': 0.0, 'activity_score': 0.0, 'finbert_score': 0.0,
                'label': 'neutral', 'confidence': 0.0,
                'transactions': [], 'scanned_at': datetime.now().isoformat(),
            }

    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps({'_ts': time.time(), 'data': results}, indent=2))
    log.info(f"[INSIDER] Scan complete — {len(results)} symbols cached.")
    return results
