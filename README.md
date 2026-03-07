# 🚀 Send It Trading

Options-first algorithmic trading system for Alpaca Markets. Three autonomous services, one repo.

| Component | Role |
|-----------|------|
| `bot/` | Intelligence pipeline — news, insider filings, Polymarket, GEX/gamma |
| `engine/` | Options-first execution engine — alpha scoring, RL threshold learning, orders |
| `dashboard/` | Live web dashboard — portfolio, P&L, plans, signals, processes |

## ⚡ Quick Start (< 10 minutes)

```bash
git clone https://github.com/jaredjester/send-it-trading.git
cd send-it-trading
bash install.sh
```

Then edit `.env`:

```env
ALPACA_LIVE_KEY=your_live_key
ALPACA_LIVE_SECRET=your_live_secret
ALPACA_MODE=live   # or paper
```

Install and start services:

```bash
sudo cp /tmp/send-it-services/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now send-it-bot send-it-engine send-it-dashboard
```

**Dashboard:** http://your-server:5555

## Requirements

- Python 3.9+
- Alpaca account (Options Level 1+, ~$200+ recommended)
- Telegram bot token (optional — for trade alerts)

## Configuration

Only two required variables. Everything else has safe defaults:

```env
ALPACA_LIVE_KEY=...
ALPACA_LIVE_SECRET=...
```

Full reference: `.env.example`

## Architecture

```
send-it-trading/
├── bot/                        # Options V1 intelligence pipeline
│   ├── main.py                 # Entry: scans → writes data/
│   └── options_v1/             # FinBERT, Kelly, pricing, execution modules
├── engine/                     # Strategy V2 options-first execution
│   ├── main_wrapper_simple.py  # Entry: reads data/ → executes trades
│   ├── orchestrator_simple.py  # Main decision loop (30-min cycles)
│   ├── core/                   # Alpaca client, options trader, dynamic config
│   ├── rl/                     # Thompson Sampling bandit — learns optimal threshold
│   ├── scanners/               # Finviz, gap scanner, catalyst scanner
│   └── evaluation/             # IC tracking, walk-forward backtester, live config
├── dashboard/                  # Flask + SSE live frontend
├── services/                   # systemd unit templates (run install.sh)
├── data/                       # Runtime intel (bot writes, engine+dashboard read)
├── engine/state/               # Runtime state (plans, trade memory, battle plan)
├── engine/logs/                # All logs (trading.log, bot.log, scanners.log)
├── .env                        # Your config (never commit)
├── .env.example                # Config template
├── install.sh                  # One-command setup
└── requirements.txt
```

## How It Works

1. **Bot** runs every 11 minutes: scans news (FinBERT NLP), insider filings, Polymarket signals, GEX gamma exposure. Writes intel to `data/`.
2. **Engine** runs every 30 minutes: reads intel, runs Finviz/gap/catalyst scanners, scores candidates via alpha engine + RL IC filtering, executes options-first (falls back to equity). Thompson Sampling bandit learns optimal entry threshold over time.
3. **Dashboard** serves live SSE updates at port 5555: portfolio P&L, open plans, signals heatmap, Polymarket feed, process monitor.

## Docker (Parallel Evolution)

Paper trading workers run in Docker for parallel parameter optimization:

```bash
docker compose up -d
```

Starts 4 paper workers (aggressive/balanced/conservative/momentum) + optimizer. Champion config auto-promoted to live engine on schedule.

## Cron Schedule (Auto-configured)

```
0 8  * * *                 alt data aggregator (Reddit/FRED/StockTwits)
50 8 * * 1-5               gap scanner (pre-market)
*/30 9-15 * * 1-5          full scanner (market hours)
30 8,11,14,16,19,22 * * 1-5  overnight optimizer
```
