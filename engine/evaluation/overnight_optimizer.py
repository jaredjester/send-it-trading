"""
Overnight parameter optimizer — v2 walk-forward + dev mode.

Modes:
  production (default): 6x/day, 60-day train window, 27 combos, 13 symbols
  dev (--dev or OPTIMIZER_DEV=1): every 5 min, 30-day train window, 9 combos, 5 symbols
    - faster iteration, separate log (optimizer_dev_log.jsonl), same live_config output
    - use to observe optimizer behavior rapidly without waiting hours
"""
import sys, json, logging, os, argparse
from datetime import datetime, timedelta
from pathlib import Path

import alpaca_env
alpaca_env.bootstrap()

# --- Dev mode detection ---
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--dev", action="store_true")
_args, _ = _parser.parse_known_args()
DEV_MODE = _args.dev or os.environ.get("OPTIMIZER_DEV", "0") == "1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()])
logger = logging.getLogger('optimizer' + ('.dev' if DEV_MODE else ''))

BASE_DIR = Path(__file__).parent.parent  # strategy_v2/
LIVE_CONFIG = BASE_DIR / 'evaluation' / 'live_config.json'
OPT_LOG = BASE_DIR / 'evaluation' / ('optimizer_dev_log.jsonl' if DEV_MODE else 'optimizer_log.jsonl')

sys.path.insert(0, str(BASE_DIR))
from evaluation.real_backtester import run_backtest

# Production config — 13 symbols, 27 combos
PROD_SYMBOLS = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN', 'JPM', 'COIN', 'PLTR']
PROD_PARAM_GRID = {
    'min_score': [63, 68, 73],
    'position_pct': [0.08, 0.10, 0.12],
    'stop_loss': [-0.06, -0.08, -0.10],
}

# Dev config — 5 symbols, 9 combos, faster per-run (~1 min vs ~4 min)
DEV_SYMBOLS = ['SPY', 'QQQ', 'AAPL', 'COIN', 'NVDA']
DEV_PARAM_GRID = {
    'min_score': [60, 65, 70],
    'position_pct': [0.08, 0.12],
    'stop_loss': [-0.06, -0.10],
}

SYMBOLS = DEV_SYMBOLS if DEV_MODE else PROD_SYMBOLS
PARAM_GRID = DEV_PARAM_GRID if DEV_MODE else PROD_PARAM_GRID

# Window config
TRAIN_DAYS = 35 if DEV_MODE else 60       # dev: 14d | prod: 60d
VALIDATE_DAYS = 20 if DEV_MODE else 30    # dev: 14d | prod: 30d
TRAIN_OFFSET = 36 if DEV_MODE else 31     # dev: 15d offset | prod: 31d offset

MIN_TRADES = 3 if DEV_MODE else 5         # dev: lower bar | prod: stricter
MIN_IMPROVEMENT = 0.05 if DEV_MODE else 0.15   # dev: 5% | prod: 15%
VALIDATION_HOLDOUT = 0.70 if DEV_MODE else 0.80


def quality_score(metrics: dict) -> float:
    """Composite quality metric."""
    s = metrics.get('sharpe', 0)
    wr = metrics.get('win_rate', 0)
    ret = metrics.get('total_return', 0)
    dd = abs(metrics.get('max_drawdown', 0.5))
    if metrics.get('num_trades', 0) < MIN_TRADES:
        return -1
    return s * wr * (1 + max(ret, 0)) * max(1 - dd, 0.1)


def load_current_config() -> dict:
    if LIVE_CONFIG.exists():
        try:
            return json.loads(LIVE_CONFIG.read_text())
        except:
            pass
    return {'min_score_threshold': 68, 'max_position_pct': 0.10, 'stop_loss_pct': -0.08, 'quality_score': 0}


def run():
    mode_tag = '[DEV MODE]' if DEV_MODE else '[PROD]'
    logger.info('=' * 60)
    logger.info(f'OVERNIGHT OPTIMIZER v2 {mode_tag} — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    logger.info(f'Symbols: {len(SYMBOLS)} | Combos: {sum(len(v) for v in PARAM_GRID.values())**len(PARAM_GRID)//max(sum(len(v) for v in PARAM_GRID.values()),1)} grid | Train: {TRAIN_DAYS}d (offset {TRAIN_OFFSET}d) | Val: {VALIDATE_DAYS}d')
    logger.info('=' * 60)

    current = load_current_config()
    current_quality = current.get('quality_score', 0)
    current_params = {
        'min_score': current.get('min_score_threshold', 68),
        'position_pct': current.get('max_position_pct', 0.10),
        'stop_loss': current.get('stop_loss_pct', -0.08)
    }

    combos = [
        (s, p, sl)
        for s in PARAM_GRID['min_score']
        for p in PARAM_GRID['position_pct']
        for sl in PARAM_GRID['stop_loss']
    ]

    logger.info(f'Phase 1: Training — {len(combos)} combos on days {TRAIN_OFFSET+TRAIN_DAYS}-{TRAIN_OFFSET} ago...')

    train_results = []
    for i, (min_score, position_pct, stop_loss) in enumerate(combos):
        params = {'min_score': min_score, 'position_pct': position_pct, 'stop_loss': stop_loss,
                  'offset_days': TRAIN_OFFSET}
        try:
            metrics = run_backtest(SYMBOLS, TRAIN_DAYS, params)
            q = quality_score(metrics)
            train_results.append({'params': params, 'metrics': metrics, 'quality': q})
            logger.info(
                f'  [{i+1}/{len(combos)}] score>={min_score} pos={position_pct:.0%} stop={stop_loss:.0%} '
                f'→ sharpe={metrics["sharpe"]:.2f} wr={metrics["win_rate"]:.0%} '
                f'trades={metrics["num_trades"]} Q={q:.3f}'
            )
        except Exception as e:
            logger.warning(f'  [{i+1}/{len(combos)}] FAILED: {e}')

    if not train_results:
        logger.error('No training results — keeping current config')
        return

    train_results.sort(key=lambda x: x['quality'], reverse=True)
    train_winner = train_results[0]

    logger.info('')
    logger.info(f'Training winner: {train_winner["params"]} | Q={train_winner["quality"]:.3f}')
    logger.info('')
    logger.info(f'Phase 2: Walk-forward validation — held-out last {VALIDATE_DAYS} days...')

    val_params = dict(train_winner['params'])
    val_params.pop('offset_days', None)

    try:
        val_metrics = run_backtest(SYMBOLS, VALIDATE_DAYS, val_params)
        val_quality = quality_score(val_metrics)
        logger.info(
            f'  Validation: sharpe={val_metrics["sharpe"]:.2f} wr={val_metrics["win_rate"]:.0%} '
            f'trades={val_metrics["num_trades"]} Q={val_quality:.3f}'
        )
    except Exception as e:
        logger.error(f'Validation backtest failed: {e} — keeping current config')
        return

    train_q = train_winner['quality']
    holdout_threshold = train_q * VALIDATION_HOLDOUT
    passes_wf = val_quality >= holdout_threshold and val_metrics['num_trades'] >= MIN_TRADES

    logger.info('')
    logger.info(f'Walk-forward: val_Q={val_quality:.3f} vs threshold={holdout_threshold:.3f} → {"PASS ✅" if passes_wf else "FAIL ❌ (overfit)"}')

    if not passes_wf:
        logger.info('Rejected — did not hold up out-of-sample. Keeping current config.')
        _write_log(train_winner, val_metrics, val_quality, current_quality, 0,
                   'rejected_overfit', current_params, train_results)
        return

    improvement = (val_quality - current_quality) / max(abs(current_quality), 0.001)
    logger.info(f'Improvement vs current: {improvement:+.1%} (threshold: {MIN_IMPROVEMENT:.0%})')

    if improvement > MIN_IMPROVEMENT and val_metrics['num_trades'] >= MIN_TRADES:
        new_config = {
            'min_score_threshold': val_params['min_score'],
            'max_position_pct': val_params['position_pct'],
            'stop_loss_pct': val_params['stop_loss'],
            'quality_score': val_quality,
            'updated_at': datetime.now().isoformat(),
            'backtest_sharpe': val_metrics['sharpe'],
            'backtest_win_rate': val_metrics['win_rate'],
            'backtest_trades': val_metrics['num_trades'],
            'train_quality': train_q,
            'val_quality': val_quality,
            'improvement_vs_baseline': improvement,
            'validation_method': 'walk_forward',
            'mode': 'dev' if DEV_MODE else 'prod',
            'train_window_days': f'{TRAIN_OFFSET+TRAIN_DAYS}-{TRAIN_OFFSET} days ago',
            'val_window_days': f'last {VALIDATE_DAYS} days'
        }
        LIVE_CONFIG.write_text(json.dumps(new_config, indent=2))
        logger.info(f'{"[DEV] " if DEV_MODE else ""}Config updated → {LIVE_CONFIG}')
        action = 'updated'
    else:
        logger.info(f'No update — improvement {improvement:+.1%} < {MIN_IMPROVEMENT:.0%} or insufficient trades')
        action = 'kept'

    _write_log(train_winner, val_metrics, val_quality, current_quality, improvement,
               action, current_params, train_results)


def _write_log(train_winner, val_metrics, val_quality, current_quality, improvement,
               action, current_params, train_results):
    OPT_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'version': 'v2_walk_forward',
        'mode': 'dev' if DEV_MODE else 'prod',
        'train_winner': {
            'params': {k: v for k, v in train_winner['params'].items() if k != 'offset_days'},
            'train_quality': train_winner['quality'],
            'train_metrics': train_winner['metrics']
        },
        'validation': {
            'val_quality': val_quality,
            'val_metrics': val_metrics,
            'holdout_threshold': train_winner['quality'] * VALIDATION_HOLDOUT,
            'passed': val_quality >= train_winner['quality'] * VALIDATION_HOLDOUT
        },
        'current_quality': current_quality,
        'improvement': improvement,
        'action': action,
        'top5_train': [{str({k: v for k, v in r["params"].items() if k != 'offset_days'}): r["metrics"]}
                       for r in train_results[:5]]
    }
    with open(OPT_LOG, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')


if __name__ == '__main__':
    run()
