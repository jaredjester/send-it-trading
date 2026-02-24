# Architecture Documentation

## System Overview

**Type:** Event-driven autonomous trading system  
**Language:** Python 3.11  
**Deployment:** Raspberry Pi (Debian ARM64)  
**API:** Alpaca Markets (live trading)

---

## Core Components

### 1. Orchestrator (`orchestrator_simple.py`)

**Responsibility:** Main trading logic and cycle coordination

**Functions:**
- Market hours checking
- Portfolio state retrieval
- Zombie position cleanup
- Opportunity scanning coordination
- Risk limit validation
- Trade execution
- Conviction management

**Cycle Flow:**
```
1. Check if market is open
2. Get portfolio state
3. Clean zombie positions (>90% loss)
4. Check conviction positions
5. Scan for opportunities (gap + catalyst)
6. Score opportunities (alpha engine)
7. Check risk limits
8. Calculate position size (Kelly)
9. Execute trades
10. Sleep 30 minutes
```

**Dependencies:**
- `core.alpaca_client.AlpacaClient`
- `conviction_manager.ConvictionManager`
- `alpha_engine.AlphaEngine` (optional)
- `scanners.morning_gap_scanner`
- `scanners.catalyst_scanner`

---

### 2. Main Wrapper (`main_wrapper_simple.py`)

**Responsibility:** Continuous operation and error handling

**Functions:**
- Initialize orchestrator
- Run 30-minute cycle loop
- Catch and log exceptions
- Graceful shutdown on interrupt

---

### 3. Alpaca Client (`core/alpaca_client.py`)

**Responsibility:** Alpaca API communication

**Methods:**
- `get_account()` - Account info
- `get_positions()` - Current positions
- `fetch_bars()` - Historical price data
- API request handling with retries

**External integration:**
- `orchestrator_simple._submit_order()` - Direct API orders

---

### 4. Conviction Manager (`conviction_manager.py`)

**Responsibility:** Manage high-conviction thesis-driven positions

**Features:**
- Conviction state persistence (`state/convictions.json`)
- Protection from standard concentration limits
- Phase tracking (ACCUMULATING, HOLDING, EXIT)
- Time decay enforcement
- PnL tracking

**Current Convictions:**
- GME: Score 82/100, Oct 2026 deadline

---

### 5. Alpha Engine (`alpha_engine.py`)

**Responsibility:** Multi-factor opportunity scoring

**Factors:**
- Sentiment (social + news)
- Technical (RSI, MACD)
- Volume (relative to average)
- Mean reversion
- Momentum
- Alternative data (optional)

**Output:** Score 0-100 (60+ = tradeable)

---

### 6. Scanners

#### Gap Scanner (`scanners/morning_gap_scanner.py`)

**Triggers:**
- Stock gaps >5% pre-market
- Volume >2x average
- Price $5-$500 (avoid penny stocks and expensive options)

**Scoring:**
- Gap size (larger = better)
- Volume ratio (higher = better)
- News presence (catalyst = better)

#### Catalyst Scanner (`scanners/catalyst_scanner.py`)

**Triggers:**
- Volume >3x average
- Fresh news (<24 hours)
- Catalyst keywords (acquisition, FDA, earnings, etc.)

**Scoring:**
- Catalyst strength
- Catalyst age (fresher = better)
- Price action (above VWAP = better)

---

## Data Flow

```
Market Opens
    ↓
Orchestrator Cycle Start
    ↓
Portfolio State ← Alpaca API
    ↓
Zombie Check → Sell Orders → Alpaca API
    ↓
Conviction Check → ConvictionManager
    ↓
Gap Scanner → Opportunities
Catalyst Scanner → Opportunities
    ↓
Alpha Engine → Scores (0-100)
    ↓
Risk Checks → Validate
    ↓
Position Sizing → Kelly Fraction
    ↓
Execute Trades → Alpaca API
    ↓
Log Everything → trading.log
    ↓
Sleep 30 Minutes
    ↓
Repeat
```

---

## Risk Controls

### Position Level
- Max 15% per symbol
- Min $10 trade notional
- Score >60 required

### Portfolio Level
- Max 95% exposure
- Min $50 cash reserve
- Zombie cleanup priority

### Conviction Level
- Override concentration limits
- Thesis-based exits only
- Time decay enforcement

---

## Error Handling

**Graceful Degradation:**
- Alpha Engine failure → Use scanner scores
- Monte Carlo failure → Use simple concentration limits
- Scanner failure → Skip scanning, log warning
- API failure → Log error, retry next cycle

**No Crash Policy:**
- Try/except around all external calls
- Log all exceptions
- Continue operation on non-critical failures

---

## Logging

**Levels:**
- INFO: Normal operation (cycles, trades, decisions)
- WARNING: Degraded mode (missing components)
- ERROR: Failures (API errors, order rejections)

**Output:**
- File: `logs/trading.log`
- Console: systemd journal
- Rotation: Manual (keep last 30 days)

---

## State Management

**Persistent State:**
- `state/convictions.json` - Active conviction positions
- `state/breaker_state.json` - Circuit breaker state (future)
- `data/trade_journal.json` - Trade history (future)

**Ephemeral State:**
- Portfolio positions (fetched each cycle)
- Market hours (checked each cycle)
- Opportunity candidates (regenerated each cycle)

---

## Configuration

**Master Config:** `master_config.json`

```json
{
  "max_position_pct": 0.15,
  "max_total_exposure": 0.95,
  "zombie_loss_threshold": -0.90,
  "min_position_value": 1.0,
  "min_cash_reserve": 50.0,
  "kelly_fraction": 0.25,
  "mc_max_drawdown": 0.375
}
```

**Environment Variables:** `.env`
- `ALPACA_API_LIVE_KEY`
- `ALPACA_API_SECRET`
- `APCA_API_KEY_ID` (alias)
- `APCA_API_SECRET_KEY` (alias)

---

## Deployment

**Service:** `mybot_full.service` (systemd)

**Auto-start:** Enabled (starts on boot)

**Restart:** Always (auto-recover from crashes)

**Environment:** Production mode (live Alpaca)

---

## Future Architecture

**Planned Enhancements:**
1. **IC Tracking:** Measure signal quality (Information Coefficient)
2. **Monte Carlo Integration:** Tail risk checking before trades
3. **Options Trading:** Spreads, covered calls
4. **Portfolio Optimization:** Rebalancing, tax-loss harvesting
5. **Machine Learning:** Adaptive signal weighting

**Archive Location:** `archive/future-features/`

---

_Last updated: 2026-02-23_
