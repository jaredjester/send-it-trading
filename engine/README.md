# Engine — Trading Strategy Core

## Module Layout

| Directory | Purpose |
|-----------|---------|
| `core/` | Infrastructure: `AlpacaClient`, `OptionsTrader`, `MonteCarloSimulator`, `dynamic_config` (centralized `cfg()`) |
| `rl/` | Reinforcement learning: `ThresholdLearner` (Thompson Sampling bandit), `EpisodeBridge`, `OnlineLearner` (per-signal IC tracking) |
| `scanners/` | Signal generation: `MorningGapScanner`, `CatalystScanner`, `FinvizScanner` |
| `evaluation/` | Performance: `BacktestEngine`, `DeploymentGate`, `AlphaTracker`, `RapidIteration`, `OvernightOptimizer` |
| `evolution/` | Docker-based parallel optimization + per-worker `live_config.json` personalities |
| `data_sources/` | Alt data: Reddit, StockTwits, Google Trends, Options Flow, FRED Macro → `AltDataAggregator` |
| `state/` | Runtime state (gitignored): `trade_memory.jsonl`, `options_plans.jsonl`, `latest_signals.json` |
| `logs/` | Runtime logs (gitignored): `trading.log`, `scanners.log` |

## Key Files

- `orchestrator_simple.py` — Master decision pipeline (score → size → execute)
- `alpha_engine.py` — Multi-factor scoring (mean reversion + momentum + sentiment)
- `main_wrapper_simple.py` — Entrypoint with signal handlers and market-hours loop

## Configuration

All tunable parameters flow through `core/dynamic_config.cfg()`:
- **Defaults** are in `dynamic_config.py` `DEFAULTS` dict
- **Overrides** are in `evaluation/live_config.json` (written by optimizer)
- **Per-worker** configs live in `evolution/workers/<name>/eval/live_config.json`

No hardcoded magic numbers. No `master_config.json` (retired).

## Architecture: Options-First

Every buy signal goes through `OptionsTrader.execute_options_buy()` first.
Falls back to stock purchase only when no viable options contract exists.
