"""
Walk-Forward Optimizer
======================
Runs weekly. Reads each worker's trade history, computes walk-forward
Sharpe ratio on the last 30-day out-of-sample window, then:

  1. Ranks workers by walk-forward Sharpe
  2. Promotes the winner's config to evaluation/live_config.json (live engine picks it up)
  3. Mutates the bottom 2 workers with crossover from the top 2
  4. Logs results to evolution/results/YYYY-MM-DD.json

Usage:
  python engine/evolution/optimizer.py          # run once
  python engine/evolution/optimizer.py --daemon  # run weekly forever
"""
import argparse
import json
import math
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent          # engine/evolution/
REPO_DIR = BASE_DIR.parent.parent                   # repo root

WORKERS_DIR   = Path(os.getenv('WORKERS_DIR',  str(REPO_DIR / 'evolution' / 'workers')))
RESULTS_DIR   = REPO_DIR / 'evolution' / 'results'
LIVE_CFG      = Path(os.getenv('EVAL_DIR', str(REPO_DIR / 'engine' / 'evaluation'))) / 'live_config.json'
CHAMPION_FILE = REPO_DIR / 'evolution' / 'champion.json'

WINDOW_DAYS   = 30   # out-of-sample evaluation window
EVAL_INTERVAL = 7    # days between evolution cycles

logging.basicConfig(level=logging.INFO, format='%(asctime)s [OPT] %(message)s')
log = logging.getLogger('optimizer')


# ─── Trade Loading ───────────────────────────────────────────────────────────

def load_worker_trades(worker_id: str, since_days: int = WINDOW_DAYS) -> list[dict]:
    """Load closed trades for a worker from its state dir."""
    plans_file = WORKERS_DIR / worker_id / 'state' / 'options_plans.jsonl'
    if not plans_file.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    trades = []
    for line in plans_file.read_text(errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
            if t.get('status') not in ('target_hit', 'stop_hit', 'near_expiry', 'oc_switch'):
                continue
            exit_ts = t.get('exit_ts') or t.get('entry_ts', '')
            if exit_ts:
                ts = datetime.fromisoformat(exit_ts.replace('Z', '+00:00'))
                if ts < cutoff:
                    continue
            trades.append(t)
        except Exception:
            pass
    return trades


# ─── Performance Metrics ─────────────────────────────────────────────────────

def compute_metrics(trades: list[dict]) -> dict:
    """Compute walk-forward performance metrics for a set of trades."""
    if not trades:
        return {'sharpe': -999, 'win_rate': 0, 'total_pnl': 0, 'n_trades': 0, 'avg_pnl': 0}

    pnls = [t.get('actual_pnl', 0) or 0 for t in trades]
    n = len(pnls)
    total = sum(pnls)
    avg = total / n
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n

    # Sharpe: annualized on daily returns (simple proxy)
    std = math.sqrt(sum((p - avg) ** 2 for p in pnls) / max(1, n - 1))
    sharpe = (avg / std * math.sqrt(252)) if std > 0 else 0.0

    # Sortino: penalizes only downside volatility
    down = [p for p in pnls if p < 0]
    down_std = math.sqrt(sum(p ** 2 for p in down) / max(1, len(down))) if down else 0
    sortino = (avg / down_std * math.sqrt(252)) if down_std > 0 else sharpe

    # Max drawdown
    equity = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / max(1, abs(peak))
        if dd > max_dd:
            max_dd = dd

    return {
        'sharpe':     round(sharpe, 4),
        'sortino':    round(sortino, 4),
        'win_rate':   round(win_rate, 4),
        'total_pnl':  round(total, 2),
        'avg_pnl':    round(avg, 2),
        'n_trades':   n,
        'max_drawdown': round(max_dd, 4),
    }


# ─── Champion Promotion ───────────────────────────────────────────────────────

def promote_champion(worker_id: str, params: dict, metrics: dict):
    """Write champion's params into live_config.json so the live engine picks them up."""
    LIVE_CFG.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Read existing live config
    live = {}
    if LIVE_CFG.exists():
        try:
            live = json.loads(LIVE_CFG.read_text())
        except Exception:
            pass

    # Apply champion params
    for key, val in params.items():
        live[key.replace('.', '_')] = val  # live_config uses underscore keys

    live['champion_worker']    = worker_id
    live['champion_promoted']  = datetime.now(timezone.utc).isoformat()
    live['champion_sharpe']    = metrics.get('sharpe')
    live['champion_win_rate']  = metrics.get('win_rate')

    LIVE_CFG.write_text(json.dumps(live, indent=2))
    log.info('Champion promoted: %s (Sharpe=%.3f, WR=%.0f%%, trades=%d)',
             worker_id, metrics['sharpe'], metrics['win_rate']*100, metrics['n_trades'])

    # Save champion record
    CHAMPION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAMPION_FILE.write_text(json.dumps({
        'worker_id': worker_id,
        'params': params,
        'metrics': metrics,
        'promoted_at': datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ─── Main Evolution Cycle ────────────────────────────────────────────────────

def run_cycle(workers: list[str] | None = None):
    """Run one walk-forward evaluation cycle."""
    from engine.evolution.params import PRESETS, mutate, crossover

    if workers is None:
        workers = [d.name for d in WORKERS_DIR.iterdir() if d.is_dir()] if WORKERS_DIR.exists() else list(PRESETS.keys())

    if not workers:
        log.warning('No workers found — nothing to evaluate')
        return

    log.info('Evaluating %d workers over last %d days: %s', len(workers), WINDOW_DAYS, workers)

    results = {}
    for wid in workers:
        trades  = load_worker_trades(wid)
        metrics = compute_metrics(trades)
        results[wid] = metrics
        log.info('  %s: Sharpe=%.3f  WR=%.0f%%  trades=%d  PnL=$%.2f',
                 wid, metrics['sharpe'], metrics['win_rate']*100,
                 metrics['n_trades'], metrics['total_pnl'])

    # Rank by Sharpe
    ranked = sorted(results.items(), key=lambda x: x[1]['sharpe'], reverse=True)
    champion_id, champion_metrics = ranked[0]

    # Require minimum trade count to be eligible
    MIN_TRADES = 3
    eligible = [(wid, m) for wid, m in ranked if m['n_trades'] >= MIN_TRADES]
    if not eligible:
        log.warning('No workers have >= %d trades yet — skipping promotion', MIN_TRADES)
    else:
        champion_id, champion_metrics = eligible[0]
        champion_params = PRESETS.get(champion_id, {})
        promote_champion(champion_id, champion_params, champion_metrics)

    # Mutate losers: replace bottom 2 with offspring of top 2
    if len(ranked) >= 4:
        top_a_params = PRESETS.get(ranked[0][0], {})
        top_b_params = PRESETS.get(ranked[1][0], {})
        loser_ids    = [ranked[-1][0], ranked[-2][0]]
        for i, loser_id in enumerate(loser_ids):
            parent_a = top_a_params if i == 0 else top_b_params
            parent_b = top_b_params if i == 0 else top_a_params
            child = mutate(crossover(parent_a, parent_b))
            PRESETS[loser_id] = child
            log.info('  Mutated %s -> new params: %s', loser_id, child)

    # Save cycle results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    out = {
        'cycle_date': stamp,
        'window_days': WINDOW_DAYS,
        'workers': results,
        'champion': champion_id,
        'ranked': [wid for wid, _ in ranked],
    }
    (RESULTS_DIR / f'{stamp}.json').write_text(json.dumps(out, indent=2))
    log.info('Cycle complete. Champion: %s', champion_id)
    return out


# ─── Daemon Mode ─────────────────────────────────────────────────────────────

def run_daemon():
    """Run optimizer weekly, forever."""
    log.info('Optimizer daemon started. Evaluating every %d days.', EVAL_INTERVAL)
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error('Cycle error: %s', e, exc_info=True)
        sleep_secs = EVAL_INTERVAL * 86400
        log.info('Next cycle in %d days', EVAL_INTERVAL)
        time.sleep(sleep_secs)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(REPO_DIR / 'engine'))

    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='Run weekly forever')
    parser.add_argument('--workers', nargs='*', help='Worker IDs to evaluate')
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    else:
        result = run_cycle(args.workers)
        if result:
            print('\nRanking:')
            for rank, wid in enumerate(result['ranked'], 1):
                m = result['workers'][wid]
                print(f'  {rank}. {wid}: Sharpe={m["sharpe"]:.3f}  WR={m["win_rate"]*100:.0f}%  trades={m["n_trades"]}  PnL=${m["total_pnl"]:.2f}')
            print(f'\nChampion: {result["champion"]}')
