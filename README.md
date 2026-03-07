# 🚀 Send It Trading

Options-first algorithmic trading system. Three components, one repo.

| Component | What it does |
|-----------|-------------|
| `bot/` | Intelligence bot — scans news, insider filings, Polymarket, gamma/GEX |
| `engine/` | Options execution engine — alpha scoring, RL threshold learning, options-first orders |
| `dashboard/` | Live web dashboard — real-time portfolio, P&L, plans, signals, RL state |

All three share `data/`, `state/`, and `logs/` at the repo root.

## Quick Start

```bash
git clone https://github.com/jaredjester/send-it-trading.git
cd send-it-trading
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set ALPACA_LIVE_KEY/SECRET and ALPACA_PAPER_KEY/SECRET (single source)

# Run each component:
python bot/main.py            # Intelligence scanner (runs overnight prep)
python engine/main_wrapper_simple.py  # Trading engine
python dashboard/api.py       # Dashboard → http://localhost:5555
```

## Configuration

Only two required env vars:

```env
ALPACA_LIVE_KEY=your_live_key
ALPACA_LIVE_SECRET=your_live_secret
ALPACA_PAPER_KEY=your_paper_key
ALPACA_PAPER_SECRET=your_paper_secret
```

Everything else has sensible defaults. Full reference in `.env.example`.

## Deployment (systemd)

```bash
# Install service files (edit User= and WorkingDirectory= to match your setup)
sudo cp services/bot.service      /etc/systemd/system/send-it-bot.service
sudo cp services/engine.service   /etc/systemd/system/send-it-engine.service
sudo cp services/dashboard.service /etc/systemd/system/send-it-dashboard.service

sudo systemctl daemon-reload
sudo systemctl enable send-it-bot send-it-engine send-it-dashboard
sudo systemctl start  send-it-bot send-it-engine send-it-dashboard
```

## Architecture

```
send-it-trading/
├── bot/                    # Options V1 intelligence pipeline
│   ├── main.py             # Entry: news/insider/gamma/polymarket scans → data/
│   └── options_v1/         # Scanner modules (FinBERT, kelly, pricing, execution)
├── engine/                 # Strategy V2 options-first execution
│   ├── main_wrapper_simple.py  # Entry: reads data/ → executes trades
│   ├── orchestrator_simple.py
│   ├── core/               # Alpaca client, options trader, dynamic config
│   ├── rl/                 # Thompson Sampling threshold learner
│   ├── scanners/           # Finviz, gap, catalyst scanners
│   └── evaluation/         # IC tracking, backtester, live config
├── dashboard/              # Flask API + live frontend
│   ├── api.py              # REST endpoints + SSE stream
│   └── templates/          # live_dashboard.html
├── services/               # systemd unit files
├── data/                   # Runtime intel (written by bot/, read by engine/ + dashboard/)
├── state/                  # Runtime state (plans, trade memory, battle plan)
├── logs/                   # Trading logs
├── .env.example            # Config template
└── requirements.txt
```

## Requirements

- Python 3.9+
- Alpaca account with Options Level 1+ enabled
- ~$200+ buying power recommended
