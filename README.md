# send-it-trading

Options-first algorithmic trading system. Three services, one repo, live on Alpaca.

---

## Mental Model

```
BOT (every 11 min)          ENGINE (every 30 min)           DASHBOARD (always on)
─────────────────           ─────────────────────           ─────────────────────
Scans the internet    ───►  Reads intel, scores              Reads everything,
Writes intel to             candidates, executes             serves live UI at
data/*.json                 options-first trades             :5555
```

**Data flow is one-way:** Bot writes → Engine reads → Dashboard reads. They never call each other.

---

## Services & Entry Points

| Service | File | Runs every |
|---------|------|-----------|
| `send-it-bot` | `bot/main.py` | 11 min |
| `send-it-engine` | `engine/main_wrapper_simple.py` | 30 min |
| `send-it-dashboard` | `dashboard/api.py` | continuous (Flask) |

---

## Directory Map

```
send-it-trading/
│
├── bot/                        BOT service
│   ├── main.py                   Entry point
│   └── options_v1/               Intelligence modules:
│       ├── news_scanner.py         FinBERT NLP on news headlines
│       ├── insider_scanner.py      SEC insider filing tracker
│       ├── polymarket_scanner.py   Prediction market signals
│       ├── gamma_scanner.py        GEX/vanna/dealer pressure
│       ├── dynamic_watchlist.py    Live symbol watchlist updater
│       ├── trade_planner.py        DCVX options trade plans (→ data/bot_trade_plans.jsonl)
│       ├── execution.py            Alpaca order submission
│       ├── kelly.py                Kelly criterion position sizing
│       ├── strategies.py           DirectionalConvex strategy logic
│       ├── rl.py                   RL trainer (adapts Kelly scale per trade)
│       └── ...                     pricing, risk, calendar, opportunity_cost
│
├── engine/                     ENGINE service
│   ├── main_wrapper_simple.py    Entry point (market-hours loop, Telegram reports)
│   ├── orchestrator_simple.py    Master decision loop:
│   │                               1. Load scanner signals + bot intel
│   │                               2. Score each candidate via alpha_engine
│   │                               3. IC-filter weak signals
│   │                               4. Size position (Kelly-inspired)
│   │                               5. Try options first → stock fallback
│   │                               6. Manage open positions (stop/target/zombie)
│   ├── alpha_engine.py           Multi-factor scorer:
│   │                               mean_reversion + momentum + sentiment
│   │                               Config from dynamic_config.cfg("alpha.*")
│   ├── core/
│   │   ├── dynamic_config.py       cfg() — SINGLE SOURCE OF TRUTH for all params
│   │   │                           Reads evaluation/live_config.json, falls back to DEFAULTS
│   │   ├── options_trader.py       Options execution (contract select, orders, plan tracking)
│   │   │                           Plans → engine/state/options_plans.jsonl
│   │   ├── alpaca_client.py        Alpaca REST wrapper (env vars only — no config files)
│   │   ├── monte_carlo.py          Risk simulation
│   │   ├── sizing.py               Kelly fraction + position sizing helpers
│   │   └── config.py               DEPRECATED stub (kept for import compat)
│   ├── rl/
│   │   ├── threshold_learner.py    Thompson Sampling bandit → learns optimal score threshold
│   │   │                           State: evaluation/threshold_bandit.json
│   │   ├── episode_bridge.py       Wires trade events into ThresholdLearner
│   │   └── online_learner.py       Per-signal IC tracking (entry/exit P&L per signal type)
│   ├── scanners/
│   │   ├── finviz_scanner.py       Finviz screen scraper → scored opportunities
│   │   ├── morning_gap_scanner.py  Pre-market gap detection (type="gap")
│   │   └── catalyst_scanner.py     Unusual volume + news catalyst detection
│   ├── data_sources/
│   │   ├── alt_data_aggregator.py  Combines all 5 alt data sources
│   │   ├── reddit_sentiment.py     r/wallstreetbets + r/stocks
│   │   ├── stocktwits_sentiment.py StockTwits sentiment labels
│   │   ├── google_trends.py        Search interest spikes
│   │   ├── options_flow.py         Put/call ratios, unusual volume
│   │   └── fred_macro.py           GDP, CPI, rates, unemployment regime
│   ├── evaluation/
│   │   ├── live_config.json        Active config overrides (written by optimizer)
│   │   ├── backtest_engine.py      Walk-forward backtester on trade_memory.jsonl
│   │   ├── deployment_gate.py      Validates changes don't degrade performance
│   │   ├── alpha_tracker.py        IC (Information Coefficient) per signal
│   │   ├── overnight_optimizer.py  Nightly parameter search
│   │   └── rapid_iteration.py      High-velocity improvement workflow
│   ├── evolution/
│   │   ├── optimizer.py            Reads worker results → promotes champion config
│   │   └── workers/
│   │       ├── aggressive/eval/live_config.json   min_score=38, max_premium=$2.00
│   │       ├── balanced/eval/live_config.json     min_score=45, max_premium=$1.50
│   │       ├── conservative/eval/live_config.json min_score=55, max_premium=$1.00
│   │       └── momentum/eval/live_config.json     min_score=40, max_premium=$2.00
│   ├── adaptive/                   DELETED — replaced by engine/rl/
│   └── state/                      Runtime (gitignored):
│       ├── trade_memory.jsonl        Every trade ever executed (engine's canonical record)
│       ├── options_plans.jsonl       Open/closed options positions (engine's options_v2)
│       ├── market_open_plan.json     Tomorrow's battle plan (overwritten each cycle)
│       └── latest_signals.json       Most recent scanner output
│
├── dashboard/
│   ├── api.py                    Flask app + SSE stream
│   └── templates/live_dashboard.html
│
├── data/                       Bot intel (bot writes, engine+dashboard read)
│   ├── news_intel.json           FinBERT-scored news headlines
│   ├── insider_intel.json        Recent SEC insider trades
│   ├── polymarket_intel.json     Prediction market signals
│   ├── gex_cache.json            Gamma exposure + VEX + dealer pressure
│   ├── sentiment_cache.json      Social sentiment by ticker
│   ├── iv_surface.json           Implied volatility surface
│   ├── synthetic_prices.json     Bot's synthetic price estimates
│   ├── bot_trade_plans.jsonl     Bot's DCVX options trades (separate schema from engine)
│   └── dynamic_watchlist.json    Live-updated symbol list
│
├── services/                   systemd unit templates (copied by install.sh)
├── alpaca_env.py               Bootstrap: maps ALPACA_LIVE_KEY → legacy env var aliases
├── docker-compose.yml          4 paper workers + optimizer (parallel param search)
├── Dockerfile                  Worker image (~800MB, no torch)
├── install.sh                  One-command setup script
├── .env                        Your secrets (never commit)
├── .env.example                All supported variables with defaults
├── requirements.txt            Engine + dashboard deps
└── requirements-worker.txt     Docker worker deps
```

---

## Configuration System

**Everything flows through `core/dynamic_config.cfg(key, default)`.**

```
Priority (high → low):
  1. evaluation/live_config.json  ← optimizer writes here
  2. DEFAULTS dict in dynamic_config.py  ← sane fallbacks for every param
```

Key namespaces:
- `alpha.*` — scoring weights, RSI thresholds, lookback periods
- `options.*` — max premium, OI filter, expiry window, stop/target pcts
- `rl_*` — RL threshold buckets, default threshold
- Everything else — position sizing, cash reserve, IC kill threshold, etc.

**Never hardcode a number in source.** If it's tunable, it belongs in `DEFAULTS`.

---

## Options-First Execution

Every `execute_buy()` call:
1. `OptionsTrader.execute_options_buy(symbol, direction, budget)` → tries to find a liquid, affordable call/put
2. If no contract found → falls back to buying stock
3. Options plans tracked in `engine/state/options_plans.jsonl`
4. Bot's DCVX plans tracked in `data/bot_trade_plans.jsonl` (different schema)

Options parameters: `options.max_premium=1.50`, `options.min_expiry_days=14`, `options.stop_loss_pct=0.50`, `options.take_profit_pct=1.00`

---

## RL Threshold Learning

`ThresholdLearner` (Thompson Sampling bandit) learns the optimal minimum score threshold per market regime (bull/bear/neutral).

- 10 buckets: `[25, 30, 35, 40, 45, 50, 55, 60, 65, 70]`
- Regime detected from portfolio P&L trend
- State persisted in `evaluation/threshold_bandit.json`
- Updated at market open (arm selection) and market close (reward signal)

---

## Docker Workers (Parallel Evolution)

```bash
docker compose up -d   # starts 4 paper workers + optimizer
```

Each worker runs the engine against paper trading with a different personality (`live_config.json`). The optimizer reads all worker `trade_memory.jsonl` files weekly and promotes the best-performing config to the live engine's `evaluation/live_config.json`.

No `CONFIG_OVERRIDE` env vars — each worker has its own committed `live_config.json`.

---

## Setup

```bash
git clone https://github.com/jaredjester/send-it-trading.git
cd send-it-trading
bash install.sh
# edit .env with your Alpaca keys
sudo systemctl enable --now send-it-bot send-it-engine send-it-dashboard
```

Required in `.env`:
```
ALPACA_LIVE_KEY=...
ALPACA_LIVE_SECRET=...
ALPACA_MODE=live        # or paper
```

Dashboard: `http://localhost:5555`

---

## What Each File Owns

| File | Owns / Writes |
|------|--------------|
| `bot/main.py` | `data/*.json` intel files |
| `engine/orchestrator_simple.py` | `engine/state/trade_memory.jsonl`, `engine/state/options_plans.jsonl`, `engine/state/latest_signals.json` |
| `engine/main_wrapper_simple.py` | `engine/state/market_open_plan.json`, Telegram messages |
| `engine/core/options_trader.py` | `engine/state/options_plans.jsonl` |
| `bot/options_v1/trade_planner.py` | `data/bot_trade_plans.jsonl` |
| `engine/evaluation/overnight_optimizer.py` | `engine/evaluation/live_config.json` |
| `engine/rl/threshold_learner.py` | `engine/evaluation/threshold_bandit.json` |
| `engine/rl/online_learner.py` | In-memory IC state (persisted via episode_bridge) |
| `dashboard/api.py` | Read-only (no writes) |
