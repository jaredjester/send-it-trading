"""
Scanner integration patch for orchestrator_simple.py

Call load_scanner_signals() at the top of each orchestration cycle.
Symbols in the scanner output get a score boost in calculate_score().

Usage in orchestrator_simple.py:
    from scanners.orchestrator_scanner_patch import load_scanner_signals, scanner_score_boost
    SCANNER_SIGNALS = load_scanner_signals()
    # Then in your scoring loop:
    score += scanner_score_boost(symbol, SCANNER_SIGNALS)
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SIGNALS_FILE = Path(__file__).parent.parent / "scanner_signals.json"

# How many points to add per scanner hit
GAP_BOOST = 15       # Gap plays: strong directional + momentum signal
CATALYST_BOOST = 20  # Catalyst plays: highest conviction, volume + news


def load_scanner_signals() -> dict:
    """
    Load scanner output from disk. Returns empty dict if no signals or stale.
    Signals older than 90 minutes are considered stale.
    """
    if not SIGNALS_FILE.exists():
        return {}

    try:
        with open(SIGNALS_FILE) as f:
            data = json.load(f)

        # Check freshness
        ts_str = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            if age_minutes > 90:
                logger.info(f"Scanner signals stale ({age_minutes:.0f} min old) — ignoring")
                return {}

        plays = data.get("all_plays", [])
        # Build lookup: symbol → list of play types
        lookup = {}
        for play in plays:
            sym = play.get("symbol", "").upper()
            if sym:
                if sym not in lookup:
                    lookup[sym] = []
                lookup[sym].append(play.get("type", "UNKNOWN"))

        if lookup:
            logger.info(f"Scanner signals loaded: {len(lookup)} symbols ({', '.join(list(lookup)[:5])})")

        return lookup

    except Exception as e:
        logger.warning(f"Failed to load scanner signals: {e}")
        return {}


def scanner_score_boost(symbol: str, scanner_signals: dict) -> int:
    """
    Return score boost for a symbol based on scanner signals.

    Args:
        symbol: Stock ticker (e.g. 'AAPL')
        scanner_signals: Dict from load_scanner_signals()

    Returns:
        Integer boost to add to signal score (0 if no scanner hit)
    """
    if not scanner_signals or not symbol:
        return 0

    play_types = scanner_signals.get(symbol.upper(), [])
    boost = 0

    for play_type in play_types:
        if play_type == "GAP":
            boost += GAP_BOOST
        elif play_type == "CATALYST":
            boost += CATALYST_BOOST

    if boost:
        logger.info(f"  Scanner boost for {symbol}: +{boost} pts ({', '.join(play_types)})")

    return min(boost, 30)  # cap at 30 points regardless of overlapping signals
