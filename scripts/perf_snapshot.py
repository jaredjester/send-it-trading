#!/usr/bin/env python3
"""
Performance Snapshot — runs daily at market close on the Pi.

Reads trade_memory.jsonl + live_config.json, captures today's metrics,
appends a record to data/perf_history.jsonl keyed to the current git SHA.

This creates a permanent time-series: every commit SHA maps to real P&L performance.
GitHub Actions reads this file to compute code ROI over time.

Run: python3 scripts/perf_snapshot.py
Cron: 30 16 * * 1-5  (4:30 PM ET weekdays, after market close)
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_DIR / "engine"
STATE_DIR = ENGINE_DIR / "state"
EVAL_DIR = ENGINE_DIR / "evaluation"
DATA_DIR = REPO_DIR / "data"

TRADE_MEMORY = STATE_DIR / "trade_memory.jsonl"
OPTIONS_PLANS = STATE_DIR / "options_plans.jsonl"
LIVE_CONFIG = EVAL_DIR / "live_config.json"
PERF_HISTORY = DATA_DIR / "perf_history.jsonl"


def get_git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"],
                           capture_output=True, text=True, cwd=REPO_DIR)
        return r.stdout.strip()[:12]
    except Exception:
        return "unknown"


def get_git_commit_count() -> int:
    try:
        r = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, cwd=REPO_DIR)
        return int(r.stdout.strip())
    except Exception:
        return 0


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def compute_metrics(trades: list, window_days: int = 7) -> dict:
    """Compute P&L metrics for the last N days of trades."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent = []

    for t in trades:
        ts_str = t.get("entry_ts") or t.get("timestamp") or t.get("created_at", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts >= cutoff:
                recent.append(t)
        except Exception:
            pass

    closed = [t for t in recent if t.get("status") not in ("open", None, "")]
    pnls = [float(t.get("pnl") or t.get("actual_pnl") or 0) for t in closed]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    n_trades = len(closed)
    win_rate = len(wins) / max(n_trades, 1)
    avg_win = sum(wins) / max(len(wins), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    profit_factor = abs(sum(wins) / sum(losses)) if losses else float("inf")

    # Sharpe approximation from daily returns
    sharpe = 0.0
    if len(pnls) >= 3:
        import statistics
        avg = statistics.mean(pnls)
        std = statistics.stdev(pnls)
        sharpe = (avg / std * (252 ** 0.5)) if std > 0 else 0.0

    # Options-specific metrics
    options_trades = [t for t in closed if t.get("strategy") in ("options_v2", "options_v1") or "occ_symbol" in t]
    options_pnl = sum(float(t.get("pnl") or 0) for t in options_trades)

    return {
        "n_trades": n_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 3),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(min(profit_factor, 99.0), 2),
        "sharpe_approx": round(sharpe, 3),
        "options_trades": len(options_trades),
        "options_pnl": round(options_pnl, 2),
        "window_days": window_days,
    }


def get_config_snapshot() -> dict:
    if not LIVE_CONFIG.exists():
        return {}
    try:
        cfg = json.loads(LIVE_CONFIG.read_text())
        return {
            "min_score_threshold": cfg.get("min_score_threshold"),
            "max_position_pct": cfg.get("max_position_pct"),
            "options_max_premium": cfg.get("options.max_premium"),
            "rl_regime": cfg.get("rl_threshold_regime"),
        }
    except Exception:
        return {}


def get_all_time_pnl(trades: list) -> dict:
    """Cumulative all-time metrics."""
    closed = [t for t in trades if t.get("status") not in ("open", None, "")]
    pnls = [float(t.get("pnl") or t.get("actual_pnl") or 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    return {
        "all_time_trades": len(closed),
        "all_time_pnl": round(sum(pnls), 2),
        "all_time_win_rate": round(len(wins) / max(len(closed), 1), 3),
    }


def get_lines_changed_since_last_snapshot(last_sha: str) -> dict:
    """Count lines added/deleted since the last snapshot SHA."""
    if not last_sha or last_sha == "unknown":
        return {"lines_added": 0, "lines_deleted": 0, "files_changed": 0, "commits_since": 0}
    try:
        current_sha = get_git_sha()
        diff = subprocess.run(
            ["git", "diff", "--shortstat", f"{last_sha}..HEAD"],
            capture_output=True, text=True, cwd=REPO_DIR
        ).stdout.strip()
        # "3 files changed, 45 insertions(+), 12 deletions(-)"
        import re
        files = int(re.search(r"(\d+) file", diff).group(1)) if "file" in diff else 0
        added = int(re.search(r"(\d+) insertion", diff).group(1)) if "insertion" in diff else 0
        deleted = int(re.search(r"(\d+) deletion", diff).group(1)) if "deletion" in diff else 0

        commits = subprocess.run(
            ["git", "rev-list", "--count", f"{last_sha}..HEAD"],
            capture_output=True, text=True, cwd=REPO_DIR
        ).stdout.strip()

        return {
            "lines_added": added,
            "lines_deleted": deleted,
            "files_changed": files,
            "commits_since": int(commits) if commits.isdigit() else 0,
        }
    except Exception:
        return {"lines_added": 0, "lines_deleted": 0, "files_changed": 0, "commits_since": 0}


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running performance snapshot...")

    # Load existing history
    history = load_jsonl(PERF_HISTORY)

    # Load trades
    trades = load_jsonl(TRADE_MEMORY)
    # Also include options plans
    plans = load_jsonl(OPTIONS_PLANS)
    all_trades = trades + [p for p in plans if p.get("status") != "open"]

    # Get last snapshot SHA for diff calculation
    last_sha = history[-1].get("git_sha", "") if history else ""

    sha = get_git_sha()
    commit_count = get_git_commit_count()

    # Compute metrics
    metrics_7d = compute_metrics(all_trades, window_days=7)
    metrics_1d = compute_metrics(all_trades, window_days=1)
    all_time = get_all_time_pnl(all_trades)
    config_snap = get_config_snapshot()
    diff_stats = get_lines_changed_since_last_snapshot(last_sha)

    # Code ROI: pnl improvement per 100 lines changed
    pnl_delta_7d = metrics_7d["total_pnl"]
    if history:
        prev_pnl_7d = history[-1].get("metrics_7d", {}).get("total_pnl", 0)
        pnl_delta_7d = metrics_7d["total_pnl"] - prev_pnl_7d

    lines_changed = diff_stats["lines_added"] + diff_stats["lines_deleted"]
    code_roi = round((pnl_delta_7d / max(lines_changed, 1)) * 100, 4)  # $/100 lines

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "commit_count": commit_count,
        "metrics_1d": metrics_1d,
        "metrics_7d": metrics_7d,
        "all_time": all_time,
        "config": config_snap,
        "diff_since_last": diff_stats,
        "pnl_delta_7d": round(pnl_delta_7d, 2),
        "code_roi_per_100_lines": code_roi,   # $ P&L change per 100 lines of code changed
    }

    # Append to history
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PERF_HISTORY, "a") as f:
        f.write(json.dumps(snapshot) + "\n")

    print(f"  Git SHA: {sha} (commit #{commit_count})")
    print(f"  7-day P&L: ${metrics_7d['total_pnl']:+.2f} | win rate: {metrics_7d['win_rate']:.0%} | trades: {metrics_7d['n_trades']}")
    print(f"  All-time: {all_time['all_time_trades']} trades | ${all_time['all_time_pnl']:+.2f}")
    print(f"  Code delta: +{diff_stats['lines_added']}/-{diff_stats['lines_deleted']} lines across {diff_stats['commits_since']} commits")
    print(f"  Code ROI: ${code_roi:+.4f} per 100 lines changed")
    print(f"  Snapshot written to {PERF_HISTORY}")

    # Git commit + push
    try:
        subprocess.run(["git", "add", str(PERF_HISTORY)], cwd=REPO_DIR, check=True)
        subprocess.run(
            ["git", "commit", "-m",
             f"perf: snapshot {sha[:8]} — 7d pnl=${metrics_7d['total_pnl']:+.2f} wr={metrics_7d['win_rate']:.0%} roi=${code_roi:+.4f}/100loc"],
            cwd=REPO_DIR, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Jared", "GIT_AUTHOR_EMAIL": "Jaredjester69@gmail.com",
                 "GIT_COMMITTER_NAME": "Jared", "GIT_COMMITTER_EMAIL": "Jaredjester69@gmail.com"}
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_DIR, check=True)
        print("  ✅ Pushed to GitHub")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠️  Git push failed: {e} (snapshot still saved locally)")


if __name__ == "__main__":
    main()
