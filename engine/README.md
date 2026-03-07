# engine/

Strategy V2 options-first execution engine. Runs every 30 minutes via `main_wrapper_simple.py`.

---

## Entry Point

```
main_wrapper_simple.py
  └── orchestrator_simple.py (SimpleOrchestrator)
        ├── Loads: scanner signals + bot intel from data/
        ├── Scores: each candidate via alpha_engine.py
        ├── Filters: IC-weak signals via rl/online_learner.py
        ├── Sizes: position via core/sizing.py
        ├── Executes: options first → stock fallback via core/options_trader.py
        └── Manages: open positions (stop loss / take profit / zombie)
```

---

## Module Reference

### `core/` — Infrastructure

| File | Purpose |
|------|---------|
| `dynamic_config.py` | **`cfg(key, default)`** — single source of truth for all params. Reads `evaluation/live_config.json`, falls back to `DEFAULTS`. |
| `options_trader.py` | Find + execute options contracts. Writes plans to `state/options_plans.jsonl`. |
| `alpaca_client.py` | Alpaca REST wrapper. Reads credentials from env vars only (`ALPACA_API_LIVE_KEY`, `ALPACA_API_SECRET`). |
| `monte_carlo.py` | P&L distribution simulation for risk checks. |
| `sizing.py` | Kelly fraction, position size helpers. |
| `config.py` | **DEPRECATED** — stub only. Use `dynamic_config.cfg()`. |

### `rl/` — Reinforcement Learning

| File | Purpose |
|------|---------|
| `threshold_learner.py` | Thompson Sampling bandit — learns optimal score threshold per regime (bull/bear/neutral). State in `evaluation/threshold_bandit.json`. |
| `episode_bridge.py` | Wires market open/close events into `ThresholdLearner` reward signals. |
| `online_learner.py` | Records entry/exit per trade, tracks per-signal IC (Information Coefficient). |

### `scanners/` — Signal Generation

| File | What it returns |
|------|----------------|
| `finviz_scanner.py` | Opportunities with `type` = screen name (e.g. `finviz_momentum`), pre-scored |
| `morning_gap_scanner.py` | Pre-market gap stocks, `type="gap"` |
| `catalyst_scanner.py` | Unusual volume + news catalyst stocks, `type` = catalyst type |

All scanner results have: `symbol`, `score`, `type` fields minimum.

### `data_sources/` — Alt Data

`alt_data_aggregator.py` combines all five into a unified composite signal (0–100):

| Source | Signal |
|--------|--------|
| `reddit_sentiment.py` | r/wallstreetbets + r/stocks mention counts + bullish/bearish |
| `stocktwits_sentiment.py` | Pre-labeled sentiment from StockTwits |
| `google_trends.py` | Search interest spike detection (0–100 scale) |
| `options_flow.py` | Put/call ratio, unusual volume via Alpaca |
| `fred_macro.py` | GDP/CPI/rates/unemployment → macro regime detection |

### `evaluation/` — Performance & Config

| File | Purpose |
|------|---------|
| `live_config.json` | **Active config overrides.** Optimizer writes here. `cfg()` reads here first. |
| `backtest_engine.py` | Walk-forward backtest on `state/trade_memory.jsonl`. |
| `deployment_gate.py` | Blocks config changes that degrade Sharpe/win-rate vs baseline. |
| `alpha_tracker.py` | Measures IC per signal type. IC < 0.03 → signal killed. |
| `overnight_optimizer.py` | Nightly parameter sweep → writes to `live_config.json`. |
| `rapid_iteration.py` | High-velocity improvement loop. |

### `evolution/` — Parallel Optimization

Docker workers each have a `workers/<name>/eval/live_config.json` with a different personality:

| Worker | min_score | max_premium | stop_loss |
|--------|-----------|-------------|-----------|
| aggressive | 38 | $2.00 | 55% |
| balanced | 45 | $1.50 | 50% |
| conservative | 55 | $1.00 | 40% |
| momentum | 40 | $2.00 | 55% |

`optimizer.py` reads worker `trade_memory.jsonl` files and promotes the best config to the live engine.

---

## Configuration

All tunable numbers live in `core/dynamic_config.py` `DEFAULTS`. Override via `evaluation/live_config.json`.

Key namespaces:
- `alpha.mean_reversion.*` — RSI, Bollinger, volume params
- `alpha.momentum.*` — SMA windows, ADX, volume growth
- `alpha.sentiment.*` — positive/negative thresholds, weight
- `options.*` — max_premium, min_open_interest, expiry window, stop/target pcts
- `rl_threshold_buckets`, `rl_default_threshold` — Thompson Sampling buckets
- `min_score_threshold`, `max_position_pct`, `min_trade_notional` — execution gates

**Rule:** If you're typing a number in source code, it probably belongs in `DEFAULTS` instead.

---

## Runtime State Files

All written at runtime, gitignored:

| File | Written by | Contains |
|------|-----------|---------|
| `state/trade_memory.jsonl` | orchestrator | Every executed trade (canonical record) |
| `state/options_plans.jsonl` | options_trader | Open/closed options positions |
| `state/market_open_plan.json` | main_wrapper | Tomorrow's ranked candidate list |
| `state/latest_signals.json` | orchestrator | Most recent scanner scores |
| `evaluation/threshold_bandit.json` | threshold_learner | Thompson Sampling posterior state |
| `evaluation/live_config.json` | overnight_optimizer | Active parameter overrides |
