#!/usr/bin/env python3
"""
Standalone scanner runner — no relative imports, designed for cron.

Writes output to: engine/scanner_signals.json (relative to repo root)
Orchestrator reads this file each cycle and boosts signal scores.

Cron schedule:
  50 8 * * 1-5   run_scanners.py --gaps      # 8:50 AM, before market open
  */30 9-15 * * 1-5  run_scanners.py --full  # Every 30 min during hours
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("run_scanners")

# ─── Path setup ───────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
STOCKBOT_DIR = SCRIPT_DIR.parent
OUTPUT_FILE = STOCKBOT_DIR / "scanner_signals.json"
LOG_FILE = Path(os.getenv('LOG_DIR', str(STOCKBOT_DIR.parent / 'engine' / 'logs'))) / 'scanners.log'

# Add engine dir + repo root to path
sys.path.insert(0, str(STOCKBOT_DIR.parent))  # ~/shared/
sys.path.insert(0, str(STOCKBOT_DIR.parent.parent))

# ─── Env loading ──────────────────────────────────────────────────────────────

def load_env():
    env_path = STOCKBOT_DIR / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))

# ─── Import scanners (absolute, not relative) ─────────────────────────────────

def import_scanners():
    import importlib.util

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    gap_mod = load("morning_gap_scanner", SCRIPT_DIR / "morning_gap_scanner.py")
    cat_mod = load("catalyst_scanner", SCRIPT_DIR / "catalyst_scanner.py")
    return gap_mod.GapScanner, cat_mod.CatalystScanner

# ─── Main ─────────────────────────────────────────────────────────────────────

def run(mode="full"):
    load_env()
    import alpaca_env
    alpaca_env.bootstrap()
    GapScanner, CatalystScanner = import_scanners()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market_date": datetime.now().strftime("%Y-%m-%d"),
        "mode": mode,
        "gap_plays": [],
        "catalyst_plays": [],
        "top_symbols": [],
    }

    # ── Gap scanner ──────────────────────────────────────────────────
    if mode in ("full", "gaps"):
        logger.info("Running gap scanner...")
        try:
            gap_scanner = GapScanner()
            gaps = gap_scanner.scan_gaps(min_gap_pct=5.0)
            results["gap_plays"] = gaps
            logger.info(f"  Gap plays found: {len(gaps)}")
            for g in gaps[:3]:
                logger.info(f"    {g['symbol']} gap={g.get('gap_pct', 0):+.1f}% vol={g.get('volume_ratio', 0):.1f}x score={g.get('score', 0):.0f}")
        except Exception as e:
            logger.error(f"Gap scanner failed: {e}")

    # ── Catalyst scanner ─────────────────────────────────────────────
    if mode in ("full", "catalysts"):
        logger.info("Running catalyst scanner...")
        try:
            cat_scanner = CatalystScanner()
            catalysts = cat_scanner.scan_catalysts(min_volume_ratio=3.0)
            results["catalyst_plays"] = catalysts
            logger.info(f"  Catalyst plays found: {len(catalysts)}")
            for c in catalysts[:3]:
                logger.info(f"    {c['symbol']} vol={c.get('volume_ratio', 0):.1f}x catalyst={c.get('catalyst_type', '?')} score={c.get('score', 0):.0f}")
        except Exception as e:
            logger.error(f"Catalyst scanner failed: {e}")

    # ── Aggregate top symbols ─────────────────────────────────────────
    all_plays = []
    for g in results["gap_plays"]:
        all_plays.append({
            "symbol": g["symbol"],
            "type": "GAP",
            "score": g.get("score", 0),
            "entry_timing": "MARKET_OPEN",
            "details": g,
        })
    for c in results["catalyst_plays"]:
        all_plays.append({
            "symbol": c["symbol"],
            "type": "CATALYST",
            "score": c.get("score", 0),
            "entry_timing": "IMMEDIATE",
            "details": c,
        })

    all_plays.sort(key=lambda x: x["score"], reverse=True)
    results["top_symbols"] = [p["symbol"] for p in all_plays[:10]]
    results["all_plays"] = all_plays

    # ── Write output ─────────────────────────────────────────────────
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Signals written → {OUTPUT_FILE}")

    # ── Print summary ─────────────────────────────────────────────────
    print(f"\n🎯 SCANNER RESULTS — {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"   Mode: {mode.upper()}")
    print(f"   Gap plays:      {len(results['gap_plays'])}")
    print(f"   Catalyst plays: {len(results['catalyst_plays'])}")
    if results["top_symbols"]:
        print(f"   Top symbols:    {', '.join(results['top_symbols'][:5])}")
    else:
        print("   No plays found (market may be closed or no setups today)")

    return results


if __name__ == "__main__":
    mode = "full"
    if "--gaps" in sys.argv:
        mode = "gaps"
    elif "--catalysts" in sys.argv:
        mode = "catalysts"

    run(mode=mode)
