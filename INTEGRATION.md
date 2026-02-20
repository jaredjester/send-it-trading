# Integrating Eval Framework with Pi Bot

**Mission:** Hook the eval framework into the live Pi bot so we measure edge and validate changes.

## Prerequisites

1. Eval framework exists: `~/.openclaw/workspace/strategy-v2/evaluation/`
2. Pi bot running: `~/shared/stockbot/`
3. Strategy V2 deployed: `~/shared/stockbot/strategy_v2/`

---

## Step 1: Deploy Eval Framework to Pi

```bash
# From Mac:
scp -r ~/.openclaw/workspace/strategy-v2/evaluation jonathangan@192.168.12.44:~/shared/stockbot/strategy_v2/

# Verify on Pi:
ssh jonathangan@192.168.12.44 "ls ~/shared/stockbot/strategy_v2/evaluation/"
# Should see: __init__.py, alpha_tracker.py, backtest_engine.py, etc.
```

---

## Step 2: Add Decision Logging to Orchestrator

Edit `~/shared/stockbot/strategy_v2/orchestrator.py`:

```python
# Add at top of file (after existing imports)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from evaluation.decision_logger import DecisionLogger
    _decision_logger = DecisionLogger(log_dir='/home/jonathangan/shared/stockbot/logs/decisions')
    _HAS_DECISION_LOGGER = True
except ImportError as e:
    logger.warning(f'Decision logger not available: {e}')
    _HAS_DECISION_LOGGER = False

# Add cycle counter at module level
_cycle_counter = 0
```

Then at the END of `run_orchestrated_cycle()` function, before the final return:

```python
async def run_orchestrated_cycle():
    global _cycle_counter
    _cycle_counter += 1
    
    logger.info("═══ Orchestrated Cycle Start ═══")
    
    # ... all existing orchestrator logic ...
    
    # NEW: Log the cycle decision
    if _HAS_DECISION_LOGGER:
        try:
            _decision_logger.log_cycle(
                cycle_number=_cycle_counter,
                portfolio_state={
                    'value': float(ps.portfolio_value),
                    'cash': float(ps.cash),
                    'positions': len(ps.positions),
                    'concentration': max([p.weight for p in ps.positions], default=0.0) if ps.positions else 0.0
                },
                signals_scored=[
                    {
                        'symbol': sym,
                        'alpha_score': score.get('alpha', 0),
                        'alt_data_boost': score.get('alt_data_boost', 0),
                        'final_score': score.get('final', 0),
                        'confidence': score.get('confidence', 0)
                    }
                    for sym, score in opportunities.items()
                ],
                rl_recommendation={
                    'action': rl_rec.get('action', 'unknown'),
                    'confidence': rl_rec.get('confidence', 0),
                    'episodes': rl_rec.get('episodes', 0)
                },
                conviction_positions=[
                    {
                        'symbol': sym,
                        'score': conv.get('current_score', 0),
                        'phase': conv.get('phase', 'UNKNOWN'),
                        'target': conv.get('target_price', 0),
                        'current': conv.get('current_price', 0),
                        'pnl_pct': ((conv.get('current_price', 0) / conv.get('entry_price', 1)) - 1) * 100 if conv.get('entry_price') else 0
                    }
                    for sym, conv in cm.convictions.items()
                ] if cm else [],
                decisions=actions_taken,  # Your list of {action, symbol, reason}
                execution_results=executed_trades,  # Your list of actual fills
                regime=current_regime,
                risk_checks={
                    'daily_loss_ok': not breaker_triggered,
                    'drawdown_ok': current_dd < 0.15,
                    'concentration_warning': max_concentration > 0.50
                }
            )
        except Exception as log_err:
            logger.error(f"Failed to log decision: {log_err}")
```

**Map your existing variables:**
- `ps` = PortfolioState object
- `opportunities` = dict of {symbol: scores}
- `rl_rec` = RL recommendation dict
- `cm` = ConvictionManager
- `actions_taken` = list of decision dicts
- `executed_trades` = list of fill dicts
- `current_regime` = regime string
- `breaker_triggered`, `current_dd`, `max_concentration` = your risk metrics

---

## Step 3: Track Signal Alpha (Future Enhancement)

This requires matching trades to their originating signals. Add later when you want to measure IC.

Create `~/shared/stockbot/strategy_v2/alpha_integration.py`:

```python
"""Track signal performance for IC measurement."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from evaluation.alpha_tracker import AlphaTracker

tracker = AlphaTracker(db_path='/home/jonathangan/shared/stockbot/evaluation/alpha_metrics.json')

def record_trade_outcome(
    signal_name: str,
    entry_signal_strength: float,
    entry_price: float,
    exit_price: float,
    days_held: int,
    benchmark_return: float
):
    """
    Call this when a trade closes.
    
    signal_name: e.g. 'alt_data_composite', 'rsi_divergence'
    entry_signal_strength: -1 to +1 signal value at entry
    entry/exit_price: actual fills
    days_held: holding period
    benchmark_return: SPY return over same period
    """
    trade_return = (exit_price / entry_price) - 1.0
    
    # Record both 1-day and multi-day returns
    tracker.record_signal_performance(
        signal_name=signal_name,
        signal_strength=entry_signal_strength,
        forward_return_1d=trade_return if days_held == 1 else trade_return / days_held,  # Approximate daily
        forward_return_5d=trade_return if days_held >= 5 else trade_return * (5 / days_held),  # Extrapolate
        benchmark_return_1d=benchmark_return
    )
```

Then call it from your trade exit logic.

---

## Step 4: Verify Decision Logging Works

After restarting bot with changes:

```bash
# Wait for one orchestrator cycle (30 min)

# Check logs were created
ssh jonathangan@192.168.12.44 "ls -lh ~/shared/stockbot/logs/decisions/"
# Should see: decisions_2026-02-19.jsonl

# View latest decision
ssh jonathangan@192.168.12.44 "tail -1 ~/shared/stockbot/logs/decisions/decisions_*.jsonl | python3 -m json.tool"
```

Expected output:
```json
{
  "timestamp": "2026-02-19T10:00:15Z",
  "cycle": 1,
  "portfolio": {
    "value": 372.15,
    "cash": 57.28,
    "positions": 14,
    "concentration": 0.69
  },
  "signals": [...],
  "rl": {...},
  "convictions": [...],
  "decisions": [...],
  "execution": [],
  "regime": "low_volatility",
  "risk": {...}
}
```

---

## Step 5: Use Deployment Gate for Changes

From your Mac (where eval framework lives):

```bash
cd ~/.openclaw/workspace/strategy-v2/evaluation

# Start interactive session
python3 -i rapid_iteration.py
```

Then in Python:

```python
# Check current signal edge
from evaluation.alpha_tracker import AlphaTracker
tracker = AlphaTracker()

print(tracker.get_edge_report())

# Propose a change
from evaluation.rapid_iteration import RapidIterationWorkflow
workflow = RapidIterationWorkflow()

# Example: Boost a signal
workflow.quick_alpha_boost_experiment('volume_spike', weight_increase=0.10)

# Or manually propose
workflow.propose_change(
    change_type='alpha_weights',
    change_params={
        'alpha_sources': {
            'sentiment': 0.20,
            'technical': 0.50,
            'volume': 0.30
        }
    },
    description="Rebalance after IC validation shows volume >> sentiment"
)
```

This will:
1. Backtest the change on 90 days
2. Compare to current baseline
3. Block if it degrades performance
4. Deploy if approved

---

## Step 6: Monitor After Deployment

```bash
# Check decision logs for anomalies
ssh pi "tail -20 ~/shared/stockbot/logs/decisions/decisions_$(date +%Y-%m-%d).jsonl | grep error"

# Check for unexpected behavior
ssh pi "grep -A5 'SKIP\|ERROR' ~/shared/stockbot/logs/decisions/decisions_*.jsonl | tail -30"
```

If something looks wrong:

```python
# Revert to backup
workflow.revert_to_backup('20260219_100000')
```

---

## What You Get

### Before:
```
Change code → Cross fingers → Deploy → (18h later: WTF happened?)
```

### After:
```
Propose change → Backtest validates → Deploy → Decision log shows exactly what bot did
```

### Metrics You Can Now Track:
- **IC per signal** (when alpha_integration added)
- **Decision patterns** (how often do we skip vs trade)
- **Regime-specific performance** (low vol vs high vol)
- **Conviction vs non-conviction returns**
- **RL override impact** (did RL improve or hurt)

### Questions You Can Answer:
- "Why did we skip NVDA on Feb 19 at 2:47 PM?" → Check decision log
- "Does volume signal actually predict?" → Check IC tracker
- "Is this config change safe?" → Deployment gate backtests it
- "What happened during that 10% drawdown day?" → Replay decisions

---

## Performance Impact

**Minimal:**
- Decision logging: ~5ms per cycle (append JSONL)
- Alpha tracking: Only on trade exits (not hot path)
- Deployment gate: Offline, doesn't touch live bot

**Disk space:**
- Decision logs: ~1-2MB/month
- Alpha metrics: ~50KB
- Backtest results: ~10MB

---

## Rollback Plan

If eval framework breaks something:

```bash
# Remove decision logger import from orchestrator
ssh pi "sed -i '/_decision_logger/d' ~/shared/stockbot/strategy_v2/orchestrator.py"

# Restart bot
ssh pi "sudo systemctl restart mybot"
```

Decision logging is optional. Bot will run fine without it.

---

## Next Steps

1. **Today:** Deploy decision logger, verify logs created
2. **This week:** Use deployment gate for next config change
3. **Next week:** Add alpha tracking for IC measurement
4. **Ongoing:** Review edge report weekly, kill dead signals

---

**This is how you go from $390 → $100K surgically.**

No more guessing. Measure edge. Validate changes. Iterate fast.
