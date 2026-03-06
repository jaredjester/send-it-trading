#!/usr/bin/env python3
"""
Main Wrapper - Simple Orchestrator
30-minute trading cycles
"""
import asyncio
import datetime
import logging
from pathlib import Path
import sys
import time
import zoneinfo

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import requests as _requests
from orchestrator_simple import SimpleOrchestrator

_TG_TOKEN = "7789884565:AAFm8-xf3zffBKvMJCen3U1B4h7Ph5UMdBU"
_TG_CHAT  = "-1002553012880"  # stockbot group channel


def _tg(msg: str):
    """Fire-and-forget Telegram message."""
    try:
        _requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=6,
        )
    except Exception:
        pass


def _cycle_report(cycle_num: int, orchestrator, secs_until_next: float):
    """Build and send a concise per-cycle Telegram update."""
    import datetime, zoneinfo
    ET  = zoneinfo.ZoneInfo("America/New_York")
    now = datetime.datetime.now(ET)

    # Portfolio snapshot (fetched by orchestrator each cycle)
    port   = getattr(orchestrator, "_last_portfolio", {}) or {}
    equity = port.get("equity", 0)
    cash   = port.get("cash",   0)
    upl    = port.get("unrealized_pl", 0)

    # Cycle stats
    is_ah  = getattr(orchestrator, "_cycle_is_afterhours", False)
    buys   = getattr(orchestrator, "_cycle_buys",  [])
    sells  = getattr(orchestrator, "_cycle_sells", [])
    top    = getattr(orchestrator, "_cycle_top",   [])
    total  = getattr(orchestrator, "_cycle_total_candidates", 0)

    # Next wake time
    next_wake = now + datetime.timedelta(seconds=secs_until_next)

    if is_ah:
        # After-hours report
        lines = [
            f"🌙 <b>After-hours #{cycle_num}</b>  {now.strftime('%H:%M')} ET",
        ]
        if equity:
            lines.append(f"💰 ${equity:,.2f} equity | ${cash:,.2f} cash")
        lines.append(f"📡 Researched {total} candidates for tomorrow")
        if top:
            lines.append("<b>Battle plan (top picks):</b>")
            for score, sym, stype in top[:5]:
                flag = "✅" if score >= 63 else "🔶" if score >= 40 else "⬜"
                stype_short = stype.replace("finviz_", "")
                lines.append(f"  {flag} {sym:8s} {score:.1f} [{stype_short}]")
        else:
            lines.append("  (no candidates above 0)")
    else:
        # Market-hours report
        lines = [
            f"🔄 <b>Cycle #{cycle_num}</b>  {now.strftime('%H:%M')} ET",
            f"💰 ${equity:,.2f} equity | ${cash:,.2f} cash | P&L {upl:+.2f}",
            f"📡 Scanned {total} candidates",
        ]
        if buys:
            lines.append("🟢 Bought: " + ", ".join(f"{s} ${n:,.0f}" for s, n in buys))
        if sells:
            lines.append("🔴 Sold: "   + ", ".join(f"{s}" for s, *_ in sells))
        if not buys and not sells:
            lines.append("⚪ No trades")
        if top:
            best_score, best_sym, best_type = top[0]
            flag = "✅" if best_score >= 63 else "🔶" if best_score >= 40 else "⬜"
            stype_short = best_type.replace("finviz_", "")
            lines.append(f"{flag} Best: {best_sym} {best_score:.1f} [{stype_short}]")

    lines.append(f"⏰ Next: {next_wake.strftime('%H:%M')} ET")
    msg = chr(10).join(lines)
    _tg(msg)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / 'logs/trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('main_wrapper')


# Priority cycle times (ET) — bot always fires at these times on weekdays
PRIORITY_TIMES_ET = [
    (9, 30),   # market open
    (15, 0),   # ~30 min before close (last signal scan)
]
ET = zoneinfo.ZoneInfo("America/New_York")


def _next_priority(now: datetime.datetime) -> datetime.datetime | None:
    """Return the next priority time that falls before now + 30 minutes."""
    window_end = now + datetime.timedelta(minutes=30)
    candidate = None
    for h, m in PRIORITY_TIMES_ET:
        # Try today first
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            # Already passed today — push to next weekday
            t += datetime.timedelta(days=1)
            while t.weekday() >= 5:
                t += datetime.timedelta(days=1)
        # Only consider it if it falls within our 30-min window
        if t < window_end:
            if candidate is None or t < candidate:
                candidate = t
    return candidate


def _sleep_seconds() -> float:
    """
    How long to sleep after a cycle completes.
    Returns seconds until the sooner of:
      - now + 30 minutes  (normal cadence)
      - next priority time (9:30 or 15:00 ET, if within 30 min)
    """
    now = datetime.datetime.now(ET)
    default_wake = now + datetime.timedelta(minutes=30)

    # Only apply priority logic on weekdays
    if now.weekday() < 5:
        priority = _next_priority(now)
        if priority is not None:
            delta_priority = (priority - now).total_seconds()
            delta_default  = (default_wake - now).total_seconds()
            if delta_priority < delta_default:
                logger.info(
                    f"Priority wake at {priority.strftime('%H:%M')} ET "
                    f"(in {delta_priority/60:.1f} min)"
                )
                return max(delta_priority, 5.0)

    return 1800.0   # 30 minutes


async def run_continuous():
    """Run trading bot continuously"""
    logger.info("=" * 80)
    logger.info("TRADING BOT - STARTING")
    logger.info("=" * 80)
    logger.info(f"Base directory: {BASE_DIR}")
    logger.info(f"Cycle interval: 30 min | Priority times: 09:30, 15:00 ET")
    logger.info("")
    
    orchestrator = SimpleOrchestrator()
    
    cycle_count = 0
    
    while True:
        cycle_count += 1
        
        try:
            logger.info(f"[Cycle #{cycle_count}]")
            await orchestrator.run_cycle()
            # Send per-cycle Telegram update
            secs = _sleep_seconds()
            _cycle_report(cycle_count, orchestrator, secs)
            
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        
        except Exception as e:
            logger.error(f"Cycle #{cycle_count} failed: {e}", exc_info=True)
        
        # Sleep until next scheduled cycle (30 min, or sooner if priority time)
        secs = _sleep_seconds()
        logger.info(f"Sleeping {secs/60:.1f} minutes...")
        logger.info("")
        await asyncio.sleep(secs)


def main():
    """Entry point"""
    try:
        asyncio.run(run_continuous())
    except KeyboardInterrupt:
        logger.info("Graceful shutdown")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
