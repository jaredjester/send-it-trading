# Deployment Plan: Eval Framework + Send It Mode

**Mission:** Deploy complete system to Pi bot. Make it live.

**Time estimate:** 30-45 minutes

---

## Phase 1: Deploy Eval Framework to Pi (10 min)

### Step 1.1: SCP Evaluation Directory
```bash
# From Mac
cd ~/.openclaw/workspace/strategy-v2

# Copy eval framework to Pi
scp -r evaluation jonathangan@192.168.12.44:~/shared/stockbot/strategy_v2/

# Verify
ssh jonathangan@192.168.12.44 "ls -lh ~/shared/stockbot/strategy_v2/evaluation/"
```

**Expected output:** See all 5 modules + README files

### Step 1.2: Test Imports on Pi
```bash
ssh jonathangan@192.168.12.44

cd ~/shared/stockbot/strategy_v2/evaluation
python3 -c "
from backtest_engine import StrategyBacktester
from alpha_tracker import AlphaTracker
from deployment_gate import DeploymentGate
from decision_logger import DecisionLogger
from rapid_iteration import RapidIterationWorkflow
print('✅ All imports working')
"
```

**If fails:** Check numpy/pandas installed. Install with `pip3 install numpy pandas`.

---

## Phase 2: Integrate Decision Logger (15 min)

### Step 2.1: Create Orchestrator Patch
```bash
# On Mac, create patch file
cat > /tmp/orchestrator_decision_patch.py << 'EOF'
"""
Patch to add decision logging to orchestrator.py
"""

IMPORTS_PATCH = """
# Decision logging (added by deployment)
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

# Cycle counter for decision logging
_cycle_counter = 0
"""

CYCLE_START_PATCH = """
    global _cycle_counter
    _cycle_counter += 1
"""

CYCLE_END_PATCH = """
    # Log orchestrator decision (added by deployment)
    if _HAS_DECISION_LOGGER:
        try:
            _decision_logger.log_cycle(
                cycle_number=_cycle_counter,
                portfolio_state={
                    'value': float(getattr(ps, 'portfolio_value', 0)),
                    'cash': float(getattr(ps, 'cash', 0)),
                    'positions': len(getattr(ps, 'positions', [])),
                },
                signals_scored=[],  # TODO: populate from orchestrator
                rl_recommendation={'action': 'unknown', 'confidence': 0},
                conviction_positions=[],  # TODO: populate from conviction_manager
                decisions=[],  # TODO: populate from orchestrator actions
                execution_results=[],
                regime='unknown',  # TODO: get from regime detector
                risk_checks={'status': 'ok'}
            )
        except Exception as log_err:
            logger.error(f"Decision log failed: {log_err}")
"""

if __name__ == '__main__':
    print("Patch strings ready. Apply manually to orchestrator.py")
EOF
```

### Step 2.2: Apply Patch to Pi
```bash
# SSH to Pi
ssh jonathangan@192.168.12.44

# Backup orchestrator
cd ~/shared/stockbot/strategy_v2
cp orchestrator.py orchestrator.py.backup_$(date +%Y%m%d_%H%M%S)

# Edit orchestrator.py
# Add imports after existing imports (around line 15)
# Add cycle counter increment at start of run_orchestrated_cycle()
# Add decision logging at end of run_orchestrated_cycle()
```

**Manual edits needed:**
1. After existing imports, add decision logger setup
2. At start of `run_orchestrated_cycle()`, add `global _cycle_counter; _cycle_counter += 1`
3. At end of `run_orchestrated_cycle()`, add decision logging call

### Step 2.3: Create Logs Directory
```bash
ssh jonathangan@192.168.12.44 "mkdir -p ~/shared/stockbot/logs/decisions"
```

---

## Phase 3: Deploy Send It Mode (10 min)

### Step 3.1: Copy Deployment Script to Pi
```bash
# From Mac
scp ~/.openclaw/workspace/strategy-v2/deploy_send_it_mode.py \
    jonathangan@192.168.12.44:~/shared/stockbot/strategy_v2/
```

### Step 3.2: Preview Changes
```bash
ssh jonathangan@192.168.12.44

cd ~/shared/stockbot/strategy_v2
python3 deploy_send_it_mode.py --show
```

**Expected:** Shows before/after comparison, exit triggers

### Step 3.3: Deploy (DRY RUN FIRST)
```bash
# Dry run
python3 deploy_send_it_mode.py
# Should say "DRY RUN MODE"

# Actually deploy
python3 deploy_send_it_mode.py --confirm
```

**Expected:**
- Backup created: `convictions.backup_TIMESTAMP.json`
- GME conviction updated:
  - `target_price: None`
  - `max_position_pct: 1.0`
  - `send_it_mode: True`

### Step 3.4: Verify Conviction File
```bash
cat ~/shared/stockbot/strategy_v2/state/convictions.json | python3 -m json.tool
```

**Check:**
- `GME.target_price` = null
- `GME.max_position_pct` = 1.0
- `GME.send_it_mode` = true

---

## Phase 4: Restart Bot (5 min)

### Step 4.1: Stop Bot
```bash
ssh jonathangan@192.168.12.44 "sudo systemctl stop mybot"
```

### Step 4.2: Check Logs Pre-Restart
```bash
ssh jonathangan@192.168.12.44 "tail -50 ~/shared/stockbot/trading.log"
```

**Look for:** Clean shutdown

### Step 4.3: Start Bot
```bash
ssh jonathangan@192.168.12.44 "sudo systemctl start mybot"
```

### Step 4.4: Watch Startup
```bash
ssh jonathangan@192.168.12.44 "journalctl -u mybot -f"
```

**Wait for:**
- Orchestrator imports successfully
- Decision logger initialized
- First cycle scheduled (30 min from now)

---

## Phase 5: Verification (5-30 min)

### Step 5.1: Check Bot Running
```bash
ssh jonathangan@192.168.12.44 "systemctl status mybot"
```

**Expected:** Active (running), recent logs

### Step 5.2: Wait for First Cycle
```bash
# Check orchestrator scheduled time
ssh jonathangan@192.168.12.44 "grep 'Next orchestrator' ~/shared/stockbot/trading.log | tail -1"
```

**Wait until that time, then check:**

### Step 5.3: Verify Decision Logs Created
```bash
ssh jonathangan@192.168.12.44 "ls -lh ~/shared/stockbot/logs/decisions/"

# View latest decision
ssh jonathangan@192.168.12.44 "tail -1 ~/shared/stockbot/logs/decisions/decisions_*.jsonl | python3 -m json.tool"
```

**Expected:**
- `decisions_2026-02-20.jsonl` exists
- JSON contains: timestamp, cycle, portfolio, signals, etc.

### Step 5.4: Verify GME Behavior
```bash
# Check conviction status
ssh jonathangan@192.168.12.44 "python3 ~/shared/stockbot/strategy_v2/check_portfolio.py"
```

**Look for:**
- GME conviction active
- No profit target
- Max position 100%
- Exit triggers: $10, $15, Oct 2026

### Step 5.5: Monitor for 1 Hour
```bash
# From Mac, run monitoring script
bash ~/.openclaw/workspace/monitor_pi_trading.sh
```

**Watch for:**
- Orchestrator cycles every 30 min
- Decision logs appending
- No early GME exit (if price moves)
- No fractional errors

---

## Rollback Plan (If Something Breaks)

### If Decision Logger Breaks:
```bash
ssh jonathangan@192.168.12.44
cd ~/shared/stockbot/strategy_v2
cp orchestrator.py.backup_TIMESTAMP orchestrator.py
sudo systemctl restart mybot
```

### If Send It Mode Breaks:
```bash
ssh jonathangan@192.168.12.44
cd ~/shared/stockbot/strategy_v2/state
cp convictions.backup_TIMESTAMP.json convictions.json
sudo systemctl restart mybot
```

### Nuclear Option (Revert Everything):
```bash
ssh jonathangan@192.168.12.44
cd ~/shared/stockbot/strategy_v2
# Remove eval framework
rm -rf evaluation/
# Restore orchestrator
cp orchestrator.py.backup_TIMESTAMP orchestrator.py
# Restore convictions
cp state/convictions.backup_TIMESTAMP.json state/convictions.json
sudo systemctl restart mybot
```

---

## Success Criteria

**✅ Deployment successful if:**
1. Bot running (systemctl status = active)
2. Decision logs created (`decisions_*.jsonl` exists)
3. GME conviction updated (target=None, max_pos=100%)
4. Orchestrator cycles every 30 min
5. No errors in logs
6. GME not exiting on arbitrary targets

**✅ System working if (24h check):**
1. Decision logs accumulating
2. GME holding through any price moves
3. No fractional errors
4. Orchestrator making decisions based on updated logic

---

## Post-Deployment Tasks

**This Week:**
- [ ] Review decision logs daily
- [ ] Verify GME holding behavior
- [ ] Monitor for any errors
- [ ] Test rapid iteration workflow on Mac

**Next Week:**
- [ ] Start tracking IC on closed trades
- [ ] Use deployment gate for next config change
- [ ] Find next conviction setup for pipeline

**This Month:**
- [ ] Weekly edge report reviews
- [ ] Kill signals with IC < 0.03
- [ ] Boost signals with IC > 0.12
- [ ] Prepare for GME exit scenarios

---

**Ready to execute. Starting Phase 1...**
