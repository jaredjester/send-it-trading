# Strategy V2 Evaluation Framework

**No more vibing. Surgical precision on edge.**

## What This Is

A complete eval system that prevents you from deploying dogshit changes and helps you find REAL alpha.

### The Problem We Solve

Before this:
- Change orchestrator logic → deploy straight to live money
- No idea if change helped or hurt
- Bug sits for 18 hours burning capital
- Can't tell which signals have edge

After this:
- Every change gets backtested on 90 days
- Deployment gate blocks degraded performance
- Alpha tracker measures IC on every signal
- Decision logs let you replay "WTF happened"

---

## Components

### 1. Backtest Engine (`backtest_engine.py`)
**What:** Historical replay of orchestrator decisions  
**Why:** Test changes before risking real money

```python
from evaluation.backtest_engine import StrategyBacktester

bt = StrategyBacktester()

# Test a config change
results = bt.run_backtest(
    start_date='2024-01-01',
    end_date='2024-12-31',
    orchestrator_config={
        'alpha_sources': {'sentiment': 0.4, 'technical': 0.6},
        'risk_limits': {'max_position': 0.35}
    }
)

print(f"Sharpe: {results['metrics']['sharpe']:.2f}")
print(f"Alpha: {results['metrics']['alpha_vs_spy']:.2%}")
```

**Database:** `evaluation/backtest_results.db`  
**Tables:** `backtest_runs`, `trade_log`, `daily_metrics`

---

### 2. Alpha Tracker (`alpha_tracker.py`)
**What:** Measures REAL edge on each signal via Information Coefficient  
**Why:** Stop weighting signals that don't predict

```python
from evaluation.alpha_tracker import AlphaTracker

tracker = AlphaTracker()

# After each trade, record signal performance
tracker.record_signal_performance(
    signal_name='rsi_divergence',
    signal_strength=0.65,  # How strong signal fired
    forward_return_1d=0.018,  # Actual 1-day return
    forward_return_5d=0.045,  # Actual 5-day return
    benchmark_return_1d=0.008  # SPY return for alpha
)

# Check if signal has proven edge
quality = tracker.get_signal_quality('rsi_divergence')
print(f"IC: {quality['ic_1d']:.3f}")
print(f"Hit Rate: {quality['hit_rate']:.1%}")
print(f"Has Edge: {quality['has_edge']}")

# Get ranked list
print(tracker.get_edge_report())
```

**Metrics:**
- **IC (Information Coefficient):** Correlation between signal strength and forward returns
  - IC > 0.15 = Strong edge
  - IC > 0.08 = Moderate edge
  - IC < 0.03 = No edge, kill signal
- **Hit Rate:** % of times signal direction matched actual move
- **Confidence:** STRONG | MODERATE | WEAK | NONE

**Storage:** `evaluation/alpha_metrics.json`

---

### 3. Deployment Gate (`deployment_gate.py`)
**What:** Validates changes before they go live  
**Why:** Blocks performance degradation

```python
from evaluation.deployment_gate import DeploymentGate

gate = DeploymentGate()

new_config = {
    'alpha_sources': {'sentiment': 0.5, 'volume': 0.5},
    'risk_limits': {'max_position': 0.40}
}

approved, reason, results = gate.validate_change(
    new_config,
    change_description="Boost volume signal weight based on IC=0.14",
    baseline_run_id="backtest_20260215_143022"  # Compare to this run
)

if approved:
    print("✅ APPROVED - deploy it")
    # ... deploy config ...
else:
    print(f"❌ REJECTED: {reason}")
```

**Validation Checks:**
1. Backtest on last 90 days
2. Compare to baseline (if provided)
3. Minimum thresholds:
   - Sharpe >= 1.0
   - Alpha > 5%
   - Max DD < 30%
   - Win rate > 45%
4. No significant degradation vs baseline

**Log:** `evaluation/deployment_log.jsonl` (append-only history)

---

### 4. Decision Logger (`decision_logger.py`)
**What:** JSONL scratchpad of every orchestrator cycle  
**Why:** Post-mortem analysis when shit breaks

```python
from evaluation.decision_logger import DecisionLogger

logger = DecisionLogger()  # Logs to logs/decisions/

# In orchestrator main loop:
logger.log_cycle(
    cycle_number=47,
    portfolio_state={'value': 372.15, 'cash': 57.28},
    signals_scored=[
        {'symbol': 'SPY', 'alpha_score': 45, 'confidence': 0.42}
    ],
    rl_recommendation={'action': 'hold', 'confidence': 0.15},
    conviction_positions=[
        {'symbol': 'GME', 'score': 76, 'phase': 'HOLDING'}
    ],
    decisions=[
        {'action': 'SKIP', 'symbol': 'SPY', 'reason': 'confidence < threshold'}
    ],
    execution_results=[],
    regime='low_volatility',
    risk_checks={'daily_loss_ok': True}
)

# Later: analyze what happened
recent = logger.get_recent_decisions(hours=24)
errors = logger.find_errors(hours=24)
pattern = logger.analyze_decision_pattern('GME', days=7)
```

**Storage:** `logs/decisions/decisions_YYYY-MM-DD.jsonl` (one file per day)

---

### 5. Rapid Iteration Workflow (`rapid_iteration.py`)
**What:** High-velocity improvement loop  
**Why:** Make changes fast, safely

```python
from evaluation.rapid_iteration import RapidIterationWorkflow

workflow = RapidIterationWorkflow()

# Boost a signal that has proven edge
workflow.quick_alpha_boost_experiment(
    signal_name='volume_spike',
    weight_increase=0.15
)
# → Validates IC > 0.10, backtests, deploys if approved

# Kill a degraded signal
workflow.kill_dead_signal('macd_cross')
# → Checks if IC < 0.03 or recent IC negative, removes if true

# Increase position size (only if strong alpha)
workflow.increase_position_size_if_alpha_strong()
# → Requires IC > 0.12 on 2+ signals

# Revert bad change
workflow.revert_to_backup('20260218_143022')
# → Restores previous config
```

**Safety:** Every change auto-validated via deployment gate

---

## Integration with Pi Bot

### Step 1: Add Decision Logging to Orchestrator

```python
# ~/shared/stockbot/strategy_v2/orchestrator.py

from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / '.openclaw/workspace/strategy-v2'))

from evaluation.decision_logger import DecisionLogger

# At module level
_decision_logger = DecisionLogger(log_dir='/home/jonathangan/shared/stockbot/logs/decisions')

# In run_orchestrated_cycle():
async def run_orchestrated_cycle():
    logger.info("═══ Orchestrated Cycle Start ═══")
    
    # ... existing logic ...
    
    # At end of cycle, log everything
    _decision_logger.log_cycle(
        cycle_number=_cycle_counter,
        portfolio_state={
            'value': ps.portfolio_value,
            'cash': ps.cash,
            'positions': len(ps.positions)
        },
        signals_scored=signals_with_scores,  # Your scored symbols
        rl_recommendation=rl_rec,
        conviction_positions=active_convictions,
        decisions=actions_taken,
        execution_results=executed_trades,
        regime=regime,
        risk_checks=risk_status
    )
```

### Step 2: Track Signal Alpha

```python
# After each trade closes, record performance
from evaluation.alpha_tracker import AlphaTracker

tracker = AlphaTracker(db_path='/home/jonathangan/shared/stockbot/evaluation/alpha_metrics.json')

# When trade exits:
tracker.record_signal_performance(
    signal_name='alt_data_composite',  # Which signal triggered trade
    signal_strength=final_signal_score,  # Score at entry
    forward_return_1d=actual_1d_return,  # Measured after trade
    forward_return_5d=actual_5d_return,
    benchmark_return_1d=spy_1d_return
)
```

### Step 3: Use Deployment Gate for Changes

```bash
# Before changing orchestrator config:
cd ~/.openclaw/workspace/strategy-v2/evaluation
python3 rapid_iteration.py
```

Then use the workflow:
```python
workflow.propose_change(
    change_type='alpha_weights',
    change_params={
        'alpha_sources': {
            'sentiment': 0.25,
            'technical': 0.50,
            'volume': 0.25
        }
    },
    description="Rebalance toward technical after IC validation"
)
```

Workflow will:
1. Backtest on 90 days
2. Compare to current baseline
3. Check IC justification
4. Deploy if approved, reject if not

---

## Workflow: Making a Change

### Before (broken):
```
1. "I think volume signals might help"
2. Edit orchestrator.py
3. Restart bot
4. Hope it works
5. (18 hours later: "why is Sharpe tanking?")
```

### After (surgical):
```
1. Check IC: tracker.get_signal_quality('volume_spike')
   → IC = 0.14, hit_rate = 58%, confidence = STRONG
   
2. Propose change: workflow.quick_alpha_boost_experiment('volume_spike', 0.15)
   → Backtests on 90 days
   → Sharpe: 1.82 (current: 1.65)
   → Alpha: +0.08 (improvement)
   → APPROVED
   
3. Deploy: auto-deployed with backup
   
4. Monitor: Check decision logs for 24h
   
5. Lock in or revert:
   - If good: keep it
   - If bad: workflow.revert_to_backup('timestamp')
```

---

## Daily Workflow

### Morning (Market Open):
```bash
# Check overnight decisions
python3 -c "
from evaluation.decision_logger import DecisionLogger
logger = DecisionLogger()
errors = logger.find_errors(hours=16)
if errors:
    print('⚠️ Errors detected:', errors)
"
```

### Weekly (Sunday):
```python
# Review signal edge
from evaluation.alpha_tracker import AlphaTracker

tracker = AlphaTracker()
print(tracker.get_edge_report())

# Kill degraded signals
for signal_name, ic in tracker.rank_signals_by_edge():
    if tracker.kill_signal_if_degraded(signal_name):
        print(f"🗑️ Kill {signal_name} - IC degraded to {ic:.3f}")
        workflow.kill_dead_signal(signal_name)
```

### Monthly:
```python
# Find new alpha
# (Run experiments, measure IC, boost winners)

# Backtest full month
bt.run_backtest('2024-01-01', '2024-01-31', current_config)
```

---

## What This Enables

### 1. Confidence in Changes
- No more "I think this will help" → Now: "IC = 0.14, 90-day backtest shows +0.15 Sharpe"

### 2. Rapid Iteration
- Before: Days between changes (fear of breaking)
- After: Multiple changes per week (validated safe)

### 3. Kill Bad Signals Fast
- Automatically detect when IC degrades
- Remove losers before they burn capital

### 4. Scale Position Size Intelligently
- Loose risk limits when alpha strong (IC > 0.12)
- Tighten when edge weakens

### 5. Post-Mortem Analysis
- "What did bot do at 2:47 PM?" → Check decision log
- "Why did we skip NVDA?" → Signal score was 0.43 < threshold

---

## Files Created

```
strategy-v2/evaluation/
├── __init__.py
├── README.md                 (this file)
├── backtest_engine.py        (8KB)
├── alpha_tracker.py          (10KB)
├── deployment_gate.py        (9KB)
├── decision_logger.py        (9KB)
└── rapid_iteration.py        (9KB)

Databases:
├── evaluation/backtest_results.db
├── evaluation/alpha_metrics.json
├── evaluation/deployment_log.jsonl
└── logs/decisions/decisions_YYYY-MM-DD.jsonl
```

Total: **~45KB code, 100% focused on measuring and improving edge**

---

## Next Steps

1. **Integrate decision logging** into orchestrator (10 min)
2. **Start tracking signal IC** for each trade (15 min)
3. **Run first backtest** on current config (5 min)
4. **Use deployment gate** for next change (instant)

---

## The Real Goal

**Turn $390 → $100K → $1M**

This framework doesn't trade for you. It tells you:
- Which signals have REAL edge (IC > 0.10)
- Which changes actually improve returns
- When to size up (strong alpha) vs size down (weak alpha)
- What went wrong when shit breaks

**With real edge measured, max risk becomes optimal Kelly, not insanity.**

$100 → $100K requires ~1000x. You've done it before.  
This gives you the precision tools to do it again. Surgically.

---

**Built:** 2026-02-19  
**For:** Surgical focus on alpha, zero tolerance for vibing
