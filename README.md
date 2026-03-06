# 🚀 Send It Trading

Options-first algorithmic trading bot with a live web dashboard. Uses Alpaca's Options API with Thompson Sampling RL to learn optimal trading thresholds.

## What It Does

- **Options-first execution** — every signal tries options first, falls back to stock
- **Multi-factor alpha scoring** — momentum, mean reversion, sentiment, Finviz signals
- **RL threshold learning** — Thompson Sampling bandit learns optimal score thresholds per market regime
- **Live dashboard** — real-time portfolio, P&L, plans, trades, signals, and RL state via SSE
- **Risk management** — max premium caps, stop losses, expiry guards, position limits

## Quick Start

```bash
# 1. Clone
git clone https://github.com/jaredjester/send-it-trading.git
cd send-it-trading

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — add your Alpaca API key + secret (minimum required config)

# 4. Run the dashboard
python dashboard_api.py
# → http://localhost:5555

# 5. Run the trading bot
python main_wrapper_simple.py
```

## Configuration

All config lives in `.env`. The only required values are your Alpaca credentials:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALPACA_API_KEY` | ✅ | — | Alpaca API key |
| `ALPACA_API_SECRET` | ✅ | — | Alpaca API secret |
| `ALPACA_PAPER` | No | `false` | Use paper trading |
| `DASHBOARD_PORT` | No | `5555` | Dashboard web port |
| `BOT_SERVICE` | No | `mybot` | Systemd service name |
| `DATA_DIR` | No | `./data` | Intel data directory |
| `STATE_DIR` | No | `./state` | Runtime state directory |
| `LOG_DIR` | No | `./logs` | Trading log directory |
| `EVAL_DIR` | No | `./evaluation` | RL/evaluation state |

### Trading Parameters

Tunable params live in `evaluation/live_config.json` and `master_config.json`. The overnight optimizer and RL threshold learner adjust these automatically.

Key params:
- `min_score_threshold` — minimum alpha score to trade (learned by RL)
- `max_premium` — max option premium per contract ($1.50 default)
- `min_open_interest` — minimum OI for contract selection (10)
- `stop_loss_pct` — stop loss percentage (-50%)
- `take_profit_pct` — take profit percentage (+100%)

## Architecture

```
send-it-trading/
├── dashboard_api.py          # Flask dashboard (REST + SSE)
├── orchestrator_simple.py    # Main trading loop
├── main_wrapper_simple.py    # Entry point with crash recovery
├── core/
│   ├── alpaca_client.py      # Alpaca API wrapper
│   ├── options_trader.py     # Options execution engine
│   └── dynamic_config.py     # Hot-reload config from live_config.json
├── rl/
│   ├── threshold_learner.py  # Thompson Sampling bandit
│   └── episode_bridge.py     # Market event → RL episode wiring
├── scanners/
│   ├── finviz_scanner.py     # Momentum/oversold/breakout/insider/PEAD
│   ├── morning_gap_scanner.py
│   └── run_scanners.py
├── evaluation/
│   ├── live_config.json      # Dynamic trading parameters
│   └── threshold_bandit.json # RL bandit state
├── data/                     # Intel data (auto-generated)
├── state/                    # Runtime state (plans, trades)
├── logs/                     # Trading logs
├── templates/
│   └── live_dashboard.html   # Dashboard frontend
├── .env.example              # Config template
├── requirements.txt
└── master_config.json        # Alpha engine weights
```

## Dashboard

The dashboard runs at `http://localhost:5555` and shows:

- **Portfolio** — positions, P&L, options vs equity split
- **Market Stance** — news sentiment, VIX regime, confidence
- **Trade Plans** — open/closed plans with thesis and P&L
- **Signal Heatmap** — per-symbol signal strength
- **RL Threshold** — Thompson Sampling bandit learning state
- **Live Logs** — real-time bot activity

All data streams via Server-Sent Events (SSE) — no polling, no WebSocket setup.

## Deployment (systemd)

```bash
# Copy the service file
sudo cp mybot.service /etc/systemd/system/mybot.service
# Edit paths in the service file to match your install location
sudo systemctl daemon-reload
sudo systemctl enable mybot
sudo systemctl start mybot

# Same for the dashboard
sudo cp dashboard.service /etc/systemd/system/dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable dashboard
sudo systemctl start dashboard
```

## Requirements

- Python 3.9+
- Alpaca account with Options Level 1+ (Level 3 recommended)
- ~$200+ buying power (works with small accounts)

## License

MIT
