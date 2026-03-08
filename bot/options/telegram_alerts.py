"""
Telegram alert module for Options V1.
Sends extreme-sentiment headlines, insider activity, and trade alerts.
"""
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────
def _load_env():
    env_file = Path('__ABSOLUTE_PATH_NEEDS_FIXING__')
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
API_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Thresholds
NEWS_EXTREME     = 0.60   # |score| above this → send headline alert
INSIDER_EXTREME  = 0.50   # |score| above this + buys/sells > 5 → send alert
VIX_ALERT_LEVEL  = 28.0   # VIX above this → send fear alert
VIX_CALM_LEVEL   = 14.0   # VIX drops below this → send calm alert

# Cooldowns — don't spam the same ticker (seconds)
_sent_cache: dict = {}
COOLDOWN_NEWS     = 3600 * 2    # 2h per ticker for news
COOLDOWN_INSIDER  = 3600 * 12   # 12h per ticker for insider
COOLDOWN_TRADE    = 60          # 1 min between any trade alerts
COOLDOWN_VIX      = 3600 * 4    # 4h for VIX alerts


def _send(text: str) -> bool:
    """Fire-and-forget Telegram send."""
    if not BOT_TOKEN or not CHAT_ID:
        log.warning('[TG] No token/chat_id configured — skipping alert')
        return False
    try:
        r = requests.post(
            API_URL,
            json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=8,
        )
        if r.status_code == 200:
            log.info('[TG] Sent: %s', text[:80])
            return True
        log.warning('[TG] HTTP %d: %s', r.status_code, r.text[:100])
        return False
    except Exception as e:
        log.warning('[TG] Send failed: %s', e)
        return False


def _cooldown_ok(key: str, cooldown: int) -> bool:
    now = time.time()
    last = _sent_cache.get(key, 0)
    if now - last < cooldown:
        return False
    _sent_cache[key] = now
    return True


def _sentiment_emoji(score: float, label: str) -> str:
    if label == 'positive' or score > 0.1:
        return '🟢' if score > 0.6 else '📈'
    if label == 'negative' or score < -0.1:
        return '🔴' if score < -0.6 else '📉'
    return '⚪'


# ── Public API ───────────────────────────────────────────

def alert_news(symbol: str, articles: list, combined_score: float, direction: str):
    """
    Send alert if any article has extreme sentiment.
    articles: list of {headline, label, score, source}
    """
    extreme = [a for a in articles if abs(a.get('score', 0)) >= NEWS_EXTREME]
    if not extreme:
        return
    if not _cooldown_ok(f'news:{symbol}', COOLDOWN_NEWS):
        return

    emoji = _sentiment_emoji(combined_score, direction)
    lines = [f"{emoji} <b>#{symbol} — News Signal</b>"]
    lines.append(f"Combined: <b>{combined_score:+.3f}</b> ({direction.upper()})")
    lines.append("")

    for a in extreme[:3]:
        sc    = a.get('score', 0)
        label = a.get('label', 'neutral')
        hl    = a.get('headline', '')[:120]
        dot   = '🟢' if label == 'positive' else '🔴'
        lines.append(f"{dot} {sc:+.2f}  {hl}")

    lines.append(f"\n<i>Source: Alpaca News + FinBERT</i>")
    _send('\n'.join(lines))


def alert_insider(symbol: str, insider_data: dict):
    """Send alert for extreme insider activity."""
    score = insider_data.get('score', 0)
    buys  = insider_data.get('buys', 0)
    sells = insider_data.get('sells', 0)
    label = insider_data.get('label', 'neutral')
    total = buys + sells

    if abs(score) < INSIDER_EXTREME or total < 3:
        return
    if not _cooldown_ok(f'insider:{symbol}', COOLDOWN_INSIDER):
        return

    emoji  = '🐂' if score > 0 else '🐻'
    dir_   = 'BUYING' if score > 0 else 'SELLING'
    lines  = [f"{emoji} <b>#{symbol} — Insider Activity</b>"]
    lines.append(f"Signal: <b>{label.upper()}</b>  score={score:+.3f}")
    lines.append(f"30-day: {buys} buys / {sells} sells")
    lines.append(f"Net shares: {insider_data.get('net_shares', 0):+,}")
    lines.append("")

    txns = insider_data.get('transactions', [])
    for t in txns[:3]:
        arrow  = '▲' if t.get('direction') == 'buy' else '▼'
        shares = f"{t.get('shares', 0):,}"
        name   = t.get('insider', '?')
        val    = t.get('value', 0)
        val_s  = f"${val/1e6:.1f}M" if val > 1e6 else f"${val:,.0f}"
        lines.append(f"  {arrow} {name} — {shares} shares ({val_s})")

    lines.append(f"\n<i>Source: SEC Form 4 / yfinance + FinBERT</i>")
    _send('\n'.join(lines))


def alert_vix(vix_level: float, regime: str, prev_regime: Optional[str] = None):
    """Send VIX regime change or extreme level alert."""
    if vix_level >= VIX_ALERT_LEVEL:
        if not _cooldown_ok('vix:fear', COOLDOWN_VIX):
            return
        lines = [
            f"⚠️ <b>VIX ELEVATED — {vix_level:.1f}</b>",
            f"Regime: <b>{regime.upper()}</b>",
            "IV multiplier boosted — position sizes reduced.",
            "<i>Options V1 Bot</i>",
        ]
        _send('\n'.join(lines))
    elif prev_regime and prev_regime in ('elevated', 'fear', 'panic') and vix_level < 20:
        if not _cooldown_ok('vix:calm', COOLDOWN_VIX):
            return
        lines = [
            f"✅ <b>VIX CALMING — {vix_level:.1f}</b>",
            f"Regime: <b>{regime.upper()}</b>",
            "Returning to normal IV scaling.",
            "<i>Options V1 Bot</i>",
        ]
        _send('\n'.join(lines))


def alert_trade(symbol: str, action: str, strategy: str, contracts: int,
                entry_price: float, ev: float, kelly: float,
                news_score: float = 0.0, insider_score: float = 0.0):
    """Send trade execution alert."""
    if not _cooldown_ok('trade:any', COOLDOWN_TRADE):
        return

    side   = '📈 CALL' if 'call' in action.lower() or action == 'buy_call' else '📉 PUT'
    lines  = [
        f"⚡ <b>TRADE PLACED — #{symbol}</b>",
        f"{side} · {strategy} · {contracts}x contracts",
        f"Entry: <b>${entry_price:.2f}</b>  EV: {ev:+.3f}  Kelly: {kelly:.3f}",
    ]
    if abs(news_score) > 0.1 or abs(insider_score) > 0.1:
        lines.append(f"Signals: news={news_score:+.3f}  insider={insider_score:+.3f}")
    lines.append("<i>Options V1 Bot · Live</i>")
    _send('\n'.join(lines))


def alert_overnight_movers(movers: list):
    """
    movers: list of {symbol, gap_pct, direction, news_score}
    Only called if |gap_pct| > 2%.
    """
    if not movers:
        return
    if not _cooldown_ok('movers:daily', 3600 * 6):
        return

    lines = ["🌙 <b>Overnight Movers</b>"]
    for m in movers[:6]:
        sym   = m['symbol']
        gap   = m['gap_pct']
        ns    = m.get('news_score', 0)
        arrow = '▲' if gap > 0 else '▼'
        emoji = '🟢' if gap > 0 else '🔴'
        lines.append(f"{emoji} <b>{sym}</b> {arrow}{abs(gap):.2f}%  news={ns:+.3f}")

    lines.append("<i>Pre-market · Options V1</i>")
    _send('\n'.join(lines))



def alert_watchlist_add(added: list, expired: list = None):
    """
    Alert when dynamic watchlist adds or expires symbols.
    added:   list of {symbol, reason, price, news_score, screener_score, ttl_days}
    expired: list of symbol strings removed (TTL ended)
    """
    if not added and not (expired or []):
        return

    lines = ["📡 <b>Watchlist Update — Options V1</b>"]

    if added:
        lines.append("")
        lines.append("✅ <b>New Scan Candidates:</b>")
        for item in added[:6]:
            sym   = item.get('symbol', '?')
            price = item.get('price', 0)
            news  = item.get('news_score', 0)
            sc    = item.get('screener_score', 0)
            ttl   = item.get('ttl_days', 3)
            reason_raw = item.get('reason', '')

            tags = []
            if sc >= 3:
                tags.append('🔥 top mover')
            elif sc >= 2:
                tags.append('📈 high activity')
            elif sc >= 1:
                tags.append('👀 on screener')
            if news >= 0.3:
                tags.append('📰 strong news +' + f'{news:.2f}')
            elif news >= 0.1:
                tags.append('📰 news +' + f'{news:.2f}')
            tag_str = '  ·  '.join(tags) if tags else reason_raw[:60]

            sym_line   = f"  <b>${sym}</b> @ ${price:.2f}  (TTL {ttl}d)"
            reason_line = f"  <i>{tag_str}</i>"
            lines.append(sym_line)
            lines.append(reason_line)

    if expired:
        lines.append("")
        lines.append("🗑 <b>Expired (TTL ended):</b>")
        exp_str = ",  ".join(f"<b>{s}</b>" for s in expired[:8])
        lines.append("  " + exp_str)

    lines.append("")
    lines.append("<i>Dynamic Watchlist · screener + news filter</i>")
    _send('\n'.join(lines))
