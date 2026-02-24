# Strategy V2 - Autonomous Trading Bot

**Status:** ✅ Operational  
**Started:** February 23, 2026  
**Current Version:** Simple Orchestrator v1.0

---

## 🎯 What This Is

Autonomous stock trading bot running on Raspberry Pi that:
- Cleans up zombie positions (>90% loss)
- Scans for high-probability opportunities (gap + catalyst)
- Manages conviction positions (GME protection)
- Executes trades via Alpaca API
- Runs 24/7 with 30-minute cycles

**Performance:** Conservative capital preservation with opportunistic entries.

---

## 🏗️ Architecture

```
orchestrator_simple.py (Main trading logic)
├── core/
│   ├── alpaca_client.py (API client)
│   ├── config.py (Config loader)
│   ├── monte_carlo.py (Tail risk simulation)
│   └── sizing.py (Kelly position sizing)
├── scanners/
│   ├── morning_gap_scanner.py (Gap opportunities)
│   └── catalyst_scanner.py (News-driven plays)
├── conviction_manager.py (GME protection)
├── alpha_engine.py (Multi-factor scoring)
└── main_wrapper_simple.py (30-min cycle wrapper)
```

---

## 🚀 Running the Bot

**Start:**
```bash
sudo systemctl start mybot_full
```

**Status:**
```bash
sudo systemctl status mybot_full
```

**Logs (live):**
```bash
journalctl -u mybot_full -f
```

**Logs (file):**
```bash
tail -f logs/trading.log
```

---

## 📊 Web Dashboard

**API:** http://192.168.12.44:5555  
**Service:** `dashboard.service`

**Endpoints:**
- `/api/health` - Service health
- `/api/portfolio` - Current positions
- `/api/convictions` - Active convictions
- `/api/status` - Trading status
- `/api/logs` - Recent log entries

---

## 🛡️ Risk Management

**Capital Preservation:**
- Max position: 15% of portfolio
- Max exposure: 95% of portfolio
- Min cash reserve: $50
- Zombie cleanup: >90% loss OR <$1 value

**Conviction Protection:**
- GME: Protected from concentration limits
- No forced exits on conviction positions
- Thesis-based exits only

---

## 🔧 Configuration

**Master config:** `master_config.json`

Key settings:
- `max_position_pct`: 0.15 (15% max per position)
- `zombie_loss_threshold`: -0.90 (90% loss = zombie)
- `min_position_value`: 1.0 ($1 minimum)
- `kelly_fraction`: 0.25 (quarter-Kelly sizing)

---

## 📁 Directory Structure

```
strategy_v2/
├── orchestrator_simple.py       # Main bot
├── main_wrapper_simple.py       # Cycle runner
├── conviction_manager.py        # GME protection
├── alpha_engine.py             # Scoring engine
├── master_config.json          # Configuration
├── core/                       # Core utilities
├── scanners/                   # Opportunity scanners
├── evaluation/                 # Performance tracking
├── data_sources/              # Alternative data
├── templates/                  # Web dashboard HTML
├── logs/                       # Trading logs
└── archive/                    # Future features
    └── future-features/        # Options, advanced risk, etc.
```

---

## 🔮 Future Features (Archived)

Located in `archive/future-features/`:
- `options_strategy.py` - Options trading
- `risk_fortress.py` - Advanced risk management
- `portfolio_optimizer.py` - Rebalancing, tax-loss harvesting
- `trade_journal.py` - Detailed audit trail

---

## 🐛 Troubleshooting

**Bot not trading?**
1. Check if market is open: `journalctl -u mybot_full | grep "Market"`
2. Check scanners: `journalctl -u mybot_full | grep "scanner"`
3. Check for errors: `journalctl -u mybot_full | grep ERROR`

**Orders rejected?**
- Zombie stocks may be untradeable (too low value)
- Check Alpaca account status
- Verify API keys in .env

**Bot crashed?**
```bash
sudo systemctl restart mybot_full
journalctl -u mybot_full -n 100
```

---

## 📝 Maintenance

**Weekly:**
- Review logs for errors
- Check portfolio performance
- Verify conviction status

**Monthly:**
- Review scanner performance (IC tracking)
- Tune thresholds if needed
- Clean up old logs

---

## 🤝 Contributing

This is a personal trading bot. Code is open-sourced for transparency and learning.

**Guidelines:**
- Keep it simple (KISS principle)
- Test before deploying
- Log everything
- Document decisions

---

## ⚠️ Disclaimer

This bot trades real money. Use at your own risk. Past performance does not guarantee future results. 

**Not financial advice.**

---

_Last updated: 2026-02-23_  
_Version: 1.0_  
_Status: Operational_
