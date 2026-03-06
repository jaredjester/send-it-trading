#!/usr/bin/env python3
"""
Finviz Scanner — Dynamic stock discovery using free Finviz screener.

6 screens:
  1. Momentum     — high relative volume + above SMA20, RSI 40-70
  2. Oversold     — RSI < 30 but above SMA200 (bounce candidates)
  3. Breakout     — new 52-week highs with above-avg volume
  4. Insider      — recent insider buying (strong niche signal)
  5. Pre-earnings — reporting today/tomorrow, good technicals (anticipation play)
  6. Post-earnings drift — reported yesterday/recent, gapped up (continuation)

Earnings plays (5 & 6) get the highest base scores — they're the most
actionable short-term catalysts available.

No API key required — uses free Finviz data.
"""

import logging
from typing import List, Dict

logger = logging.getLogger("finviz_scanner")

# Base scores — overridden by alpha engine but used as floor on failure
SCREEN_BASE_SCORES = {
    "finviz_momentum":        66,
    "finviz_oversold":        64,
    "finviz_breakout":        67,
    "finviz_insider":         65,
    "finviz_preearnings":     70,   # Highest — known catalyst coming
    "finviz_postearnings":    69,   # High — confirmed beat + drift
    "finviz_relstrength":     66,   # Up on red day = institutional buying
}

# Max candidates per screen
MAX_PER_SCREEN = 8
MAX_TOTAL = 25  # Raised slightly to accommodate earnings plays


def _safe_screen(screen_fn, screen_name: str) -> List[Dict]:
    """Run a screen function, return [] on any failure."""
    try:
        return screen_fn()
    except Exception as e:
        logger.debug(f"Finviz {screen_name} screen failed: {e}")
        return []


def _screen_momentum() -> List[Dict]:
    """Momentum plays: above SMA20, RSI neutral, liquid.
    Rel Vol dropped — it kills results on red days when good stocks
    hold their SMA20 quietly. Alpha engine re-scores everything anyway.
    """
    from finvizfinance.screener.technical import Technical
    ft = Technical()
    ft.set_filter(filters_dict={
        "20-Day Simple Moving Average": "Price above SMA20",
        "RSI (14)":                     "Not Overbought (<60)",  # RSI below 60 (not in top decile)
        "Average Volume":               "Over 500K",
        "Price":                        "Over $5",
    })
    df = ft.screener_view()
    results = []
    for _, row in df.head(MAX_PER_SCREEN).iterrows():
        sym = str(row.get("Ticker", "")).strip()
        if not sym:
            continue
        results.append({
            "symbol": sym,
            "score": SCREEN_BASE_SCORES["finviz_momentum"],
            "type": "finviz_momentum",
            "reason": f"RelVol>2x, above SMA20, RSI neutral",
        })
    return results


def _screen_oversold() -> List[Dict]:
    """RSI oversold bounce plays.
    Relaxed from SMA200 to SMA50 — real bounces happen at intermediate support too.
    Two sub-screens: structurally sound (above SMA50) and deep oversold (below SMA50).
    """
    from finvizfinance.screener.technical import Technical
    results = []
    for sma_filter, label, base_score in [
        ("Price above SMA50",  "above SMA50, oversold",  65),
        ("Price below SMA50",  "deep oversold bounce",   62),
    ]:
        try:
            ft = Technical()
            ft.set_filter(filters_dict={
                "RSI (14)":                         "Oversold (30)",
                "50-Day Simple Moving Average":     sma_filter,
                "Average Volume":                   "Over 300K",
                "Price":                            "Over $2",
            })
            df = ft.screener_view()
            for _, row in df.head(MAX_PER_SCREEN // 2).iterrows():
                sym = str(row.get("Ticker", "")).strip()
                if not sym:
                    continue
                results.append({
                    "symbol": sym,
                    "score":  base_score,
                    "type":   "finviz_oversold",
                    "reason": f"RSI oversold, {label}",
                })
        except Exception as e:
            logger.debug(f"Oversold sub-screen: {e}")
    return results
    df = ft.screener_view()
    results = []
    for _, row in df.head(MAX_PER_SCREEN).iterrows():
        sym = str(row.get("Ticker", "")).strip()
        if not sym:
            continue
        results.append({
            "symbol": sym,
            "score": SCREEN_BASE_SCORES["finviz_oversold"],
            "type": "finviz_oversold",
            "reason": "RSI oversold, above SMA200 (bounce)",
        })
    return results


def _screen_breakout() -> List[Dict]:
    """Breakout plays: new 52wk highs OR near highs (within 5%).
    Two sub-screens to catch both confirmed breakouts and near-breakouts.
    Rel Vol dropped — alpha engine handles quality filtering.
    """
    from finvizfinance.screener.technical import Technical
    results = []
    for high_filter, label in [
        ("New High",              "52wk breakout"),
        ("5% or more below High", "near 52wk high (within 5%)"),
    ]:
        try:
            ft = Technical()
            ft.set_filter(filters_dict={
                "52-Week High/Low": high_filter,
                "Average Volume":   "Over 300K",
                "Price":            "Over $5",
            })
            df = ft.screener_view()
            for _, row in df.head(MAX_PER_SCREEN // 2).iterrows():
                sym = str(row.get("Ticker", "")).strip()
                if not sym:
                    continue
                results.append({
                    "symbol": sym,
                    "score":  SCREEN_BASE_SCORES["finviz_breakout"],
                    "type":   "finviz_breakout",
                    "reason": f"Breakout: {label}",
                })
        except Exception as e:
            logger.debug(f"Breakout sub-screen ({high_filter}): {e}")
    return results


def _screen_insider_buying() -> List[Dict]:
    """Recent insider buying — strong signal for under-the-radar stocks."""
    from finvizfinance.insider import Insider
    fi = Insider(option="latest")
    df = fi.get_insider()
    if df is None or df.empty:
        return []

    # Filter for buys only
    buys = df[df.get("Transaction", df.get("Type", df.columns[0])).astype(str).str.contains(
        "Buy|Purchase", case=False, na=False
    )]

    seen = set()
    results = []
    for _, row in buys.head(20).iterrows():
        sym = str(row.get("Ticker", "")).strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        owner = row.get("Owner", row.get("Insider", "unknown"))
        relationship = row.get("Relationship", "")
        results.append({
            "symbol": sym,
            "score": SCREEN_BASE_SCORES["finviz_insider"],
            "type": "finviz_insider",
            "reason": f"Insider buy: {owner} ({relationship})",
        })
        if len(results) >= MAX_PER_SCREEN:
            break
    return results



def _screen_relative_strength() -> List[Dict]:
    """
    Relative strength screen: stocks UP today while market sells off.
    These are the market leaders — institutions are buying them into weakness.
    Works specifically on red market days when other screens go quiet.
    """
    from finvizfinance.screener.performance import Performance
    try:
        fp = Performance()
        fp.set_filter(filters_dict={
            "Performance":    "Today +5%",   # up 5%+ today (valid value)
            "Average Volume": "Over 300K",
            "Price":          "Over $5",
        })
        df = fp.screener_view()
        results = []
        for _, row in df.head(MAX_PER_SCREEN).iterrows():
            sym = str(row.get("Ticker", "")).strip()
            if not sym:
                continue
            results.append({
                "symbol": sym,
                "score":  SCREEN_BASE_SCORES.get("finviz_relstrength", 66),
                "type":   "finviz_relstrength",
                "reason": "Up on red day — relative strength leader",
            })
        return results
    except Exception as e:
        logger.debug(f"Relative strength screen: {e}")
        return []

def _screen_preearnings() -> List[Dict]:
    """
    Pre-earnings anticipation plays.
    Find stocks reporting Today After Close or Tomorrow with:
    - RSI not overbought (room to run into print)
    - Relative volume elevated (smart money positioning)
    - Above SMA20 (uptrend — earnings beats reward uptrending stocks more)

    Why it works: stocks in uptrends that beat earnings continue higher.
    Stocks in downtrends that beat earnings often fade. So we want the setup.
    """
    from finvizfinance.screener.technical import Technical
    results = []

    # Two sub-screens: tonight's reports + tomorrow's reports
    for earn_filter, label in [
        ("Today After Market Close", "reports tonight"),
        ("Tomorrow Before Market Open", "reports tomorrow AMO"),
        ("Tomorrow After Market Close", "reports tomorrow AMC"),
    ]:
        try:
            ft = Technical()
            ft.set_filter(filters_dict={
                "Earnings Date":                earn_filter,
                "RSI (14)":                     "Not Overbought (<60)",
                "20-Day Simple Moving Average": "Price above SMA20",
                "Average Volume":               "Over 200K",
                "Price":                        "Over $3",
            })
            # Note: using "Not Overbought (<60)" — the only valid RSI range filter
            df = ft.screener_view()
            for _, row in df.head(MAX_PER_SCREEN).iterrows():
                sym = str(row.get("Ticker", "")).strip()
                if not sym:
                    continue
                results.append({
                    "symbol": sym,
                    "score":  SCREEN_BASE_SCORES["finviz_preearnings"],
                    "type":   "finviz_preearnings",
                    "reason": f"Earnings {label}, above SMA20, RSI neutral, rel vol up",
                })
        except Exception as e:
            logger.debug(f"Pre-earnings sub-screen ({earn_filter}): {e}")

    return results


def _screen_postearnings_drift() -> List[Dict]:
    """
    Post-earnings drift plays.
    Find stocks that reported Yesterday or in the Previous 5 Days where:
    - Price is above SMA20 (survived the announcement, uptrend intact)
    - Relative volume still elevated (continuation interest)
    - RSI 45-70 (momentum building, not overbought)

    Why it works: stocks that gap up on earnings often drift higher for
    2-5 sessions as analysts upgrade and institutions add exposure.
    This is a well-documented anomaly (PEAD — Post-Earnings Announcement Drift).
    """
    from finvizfinance.screener.technical import Technical
    results = []

    for earn_filter, label in [
        ("Yesterday",       "reported yesterday"),
        ("Previous 5 Days", "reported last 5 days"),
    ]:
        try:
            ft = Technical()
            ft.set_filter(filters_dict={
                "Earnings Date":                earn_filter,
                "20-Day Simple Moving Average": "Price above SMA20",
                "RSI (14)":                     "Not Overbought (<60)",
                "Average Volume":               "Over 300K",
                "Price":                        "Over $3",
            })
            df = ft.screener_view()
            for _, row in df.head(MAX_PER_SCREEN).iterrows():
                sym = str(row.get("Ticker", "")).strip()
                if not sym:
                    continue
                results.append({
                    "symbol": sym,
                    "score":  SCREEN_BASE_SCORES["finviz_postearnings"],
                    "type":   "finviz_postearnings",
                    "reason": f"PEAD: {label}, above SMA20, RSI building",
                })
        except Exception as e:
            logger.debug(f"Post-earnings sub-screen ({earn_filter}): {e}")

    return results


def run_finviz_scan() -> List[Dict]:
    """
    Main entry point. Runs all 6 screens and deduplicates results.
    Symbols in multiple screens get score boost.
    Writes results to state/latest_signals.json for the options bot to read.

    Returns: list of opportunity dicts for orchestrator_simple.py
    """
    results: List[Dict] = []
    symbol_scores: Dict[str, Dict] = {}

    screens = [
        ("momentum",     _screen_momentum),
        ("oversold",     _screen_oversold),
        ("breakout",     _screen_breakout),
        ("insider",      _screen_insider_buying),
        ("preearnings",  _screen_preearnings),
        ("postearnings", _screen_postearnings_drift),
        ("relstrength", _screen_relative_strength),
    ]

    for name, fn in screens:
        hits = _safe_screen(fn, name)
        logger.info(f"Finviz {name}: {len(hits)} hits")
        for h in hits:
            sym = h["symbol"]
            if sym in symbol_scores:
                # Symbol appeared in multiple screens — boost score
                existing = symbol_scores[sym]
                existing["score"] = min(existing["score"] + 3, 80)
                existing["reason"] += f" + {name}"
            else:
                symbol_scores[sym] = h.copy()

    results = list(symbol_scores.values())

    # Sort by score descending, cap total
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:MAX_TOTAL]

    logger.info(f"Finviz total: {len(results)} unique candidates")

    # Write to shared signal bus so options bot can read without re-querying Finviz
    try:
        import json as _json, os as _os
        from datetime import datetime as _dt
        _state_dir = _os.path.join(_os.path.dirname(__file__), '..', 'state')
        _os.makedirs(_state_dir, exist_ok=True)
        _bus_path = _os.path.join(_state_dir, 'latest_signals.json')
        with open(_bus_path, 'w') as _f:
            _json.dump({
                'updated_at': _dt.now().isoformat(),
                'signals': results,
            }, _f, indent=2)
    except Exception as _e:
        logger.debug(f"Signal bus write failed: {_e}")

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    hits = run_finviz_scan()
    for h in hits:
        print(f"{h['symbol']:8s} score={h['score']} type={h['type']} | {h['reason']}")
