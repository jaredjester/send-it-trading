# Contributing Guide

## Philosophy

**Keep it simple.** This bot trades real money. Simple code = fewer bugs = less risk.

**Log everything.** If you can't see it in logs, it didn't happen.

**Test before deploying.** No live testing in production.

---

## Code Style

**Python:**
- Python 3.11+
- Type hints optional but encouraged
- Docstrings for public functions
- Max line length: 100 chars

**Naming:**
- `snake_case` for functions and variables
- `PascalCase` for classes
- `UPPER_CASE` for constants

**Imports:**
```python
# Standard library
import asyncio
import logging
from datetime import datetime

# Third party
import pandas as pd
import requests

# Local
from core.alpaca_client import AlpacaClient
from conviction_manager import ConvictionManager
```

---

## Project Structure

```
strategy_v2/
├── orchestrator_simple.py       # ⭐ Main bot (DO NOT DELETE)
├── main_wrapper_simple.py       # ⭐ Cycle runner (DO NOT DELETE)
├── conviction_manager.py        # ⭐ GME protection (ACTIVE)
├── alpha_engine.py             # ⭐ Scoring (ACTIVE)
├── master_config.json          # ⭐ Configuration (ACTIVE)
├── core/                       # Core utilities
│   ├── alpaca_client.py        # ⭐ API client (ACTIVE)
│   ├── config.py               # Config loader
│   ├── monte_carlo.py          # Tail risk simulation
│   └── sizing.py               # Kelly sizing
├── scanners/                   # Opportunity scanners
│   ├── morning_gap_scanner.py  # ⭐ Gap scanner (ACTIVE)
│   ├── catalyst_scanner.py     # ⭐ Catalyst scanner (ACTIVE)
│   └── opportunity_finder.py   # Combined scanner
├── evaluation/                 # Performance tracking
│   ├── alpha_tracker.py        # IC measurement
│   ├── backtest_engine.py      # Historical testing
│   ├── decision_logger.py      # Audit trail
│   └── ic_integration.py       # Signal tracking
├── data_sources/              # Alternative data
│   ├── alt_data_aggregator_safe.py  # Working sources
│   └── (various scrapers)
├── analytics/                  # Performance analysis
│   └── profit_tracker.py
├── tests/                      # Unit tests
├── logs/                       # Trading logs
└── archive/                    # Future features
    └── future-features/        # Options, advanced features
```

⭐ = ACTIVE (do not delete/modify without testing)

---

## Development Workflow

### 1. Make Changes Locally

Clone to Mac for development:
```bash
cd ~/.openclaw/workspace/strategy-v2
# Make changes
# Test locally (paper trading)
```

### 2. Test Before Deploy

**Syntax check:**
```bash
python3 -m py_compile your_file.py
```

**Import check:**
```bash
python3 -c "from your_module import YourClass"
```

**Dry run:**
```bash
python3 orchestrator_simple.py  # Should run one cycle
```

### 3. Deploy to Pi

```bash
# Copy changes
scp your_file.py jonathangan@192.168.12.44:~/shared/stockbot/strategy_v2/

# SSH to Pi
ssh jonathangan@192.168.12.44

# Test
cd ~/shared/stockbot/strategy_v2
python3 -c "import your_module"

# Restart bot
sudo systemctl restart mybot_full

# Watch logs
journalctl -u mybot_full -f
```

### 4. Verify

Watch for 2-3 cycles (1-1.5 hours) to ensure no errors.

---

## Adding New Features

### New Scanner

1. Create `scanners/your_scanner.py`
2. Implement `run_your_scan()` function
3. Return list of opportunities with `symbol`, `score`, etc.
4. Import in `orchestrator_simple.py`
5. Add to `scan_opportunities()` method
6. Test on paper account first

### New Risk Check

1. Add method to `orchestrator_simple.py`
2. Call in `check_risk_limits()`
3. Log when check fails
4. Test with edge cases

### New Data Source

1. Create `data_sources/your_source.py`
2. Implement scraper with error handling
3. Add to `alt_data_aggregator_safe.py`
4. Test API limits carefully

---

## Testing

### Manual Testing

```bash
# Syntax
python3 -m py_compile *.py

# Imports
python3 -c "from orchestrator_simple import SimpleOrchestrator"

# One cycle (dry run)
python3 orchestrator_simple.py
```

### Unit Tests

```bash
cd tests/
pytest test_your_module.py
```

### Integration Test

```bash
# Run one full cycle in paper mode
# (Requires paper trading API keys)
```

---

## Deployment Checklist

Before deploying to production:

- [ ] Code compiles without syntax errors
- [ ] All imports resolve
- [ ] Logs show expected behavior
- [ ] Tested on paper account
- [ ] No breaking changes to config
- [ ] Backward compatible with existing state files
- [ ] Documentation updated

---

## Emergency Procedures

### Bot Crashed

```bash
# Check status
sudo systemctl status mybot_full

# Check recent logs
journalctl -u mybot_full -n 100

# Restart
sudo systemctl restart mybot_full

# Watch for errors
journalctl -u mybot_full -f
```

### Bad Deploy

```bash
# Rollback to previous version
cd ~/shared/stockbot/strategy_v2
git log --oneline -5  # Find last good commit
git checkout <commit-hash> -- orchestrator_simple.py
sudo systemctl restart mybot_full
```

### Runaway Trading

```bash
# Stop bot immediately
sudo systemctl stop mybot_full

# Close all positions manually via Alpaca web interface
# Investigate logs
# Fix issue
# Test thoroughly before restarting
```

---

## Best Practices

### Logging

**DO:**
```python
logger.info(f"✓ Executed trade: {symbol} ${notional:.2f}")
logger.warning(f"⚠️ Risk check failed: {reason}")
logger.error(f"❌ API call failed: {error}")
```

**DON'T:**
```python
print("Trading...")  # Not captured by systemd
logger.info("Starting")  # Too vague
```

### Error Handling

**DO:**
```python
try:
    result = risky_operation()
except SpecificException as e:
    logger.error(f"Operation failed: {e}")
    return fallback_value
```

**DON'T:**
```python
try:
    result = risky_operation()
except:
    pass  # Silent failure = debugging hell
```

### Configuration

**DO:**
- Use `master_config.json` for parameters
- Load config at startup
- Validate config values

**DON'T:**
- Hardcode magic numbers
- Change config without documenting
- Deploy config changes without testing

---

## Documentation Standards

### Code Comments

Comment the "why", not the "what":

**GOOD:**
```python
# Alpaca rejects orders <$1, so we skip these zombies
if market_value < 1.0:
    continue
```

**BAD:**
```python
# Check if market value is less than 1
if market_value < 1.0:
    continue
```

### Commit Messages

Format:
```
<type>: <subject>

<body>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

**Example:**
```
fix: Handle rejected orders in zombie cleanup

Alpaca was accepting orders but rejecting them later.
Bot thought trades succeeded. Now we check order status
after submission and log rejections properly.

Fixes issue with BGXXQ and MOTS positions.
```

---

## Questions?

Check existing code for examples. When in doubt, keep it simple.

**Remember:** This bot trades real money. Be careful.
