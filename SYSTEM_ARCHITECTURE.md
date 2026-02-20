# 🏗️ Risk Fortress System Architecture

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRADING BOT (Your Code)                     │
│                                                                   │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐              │
│  │ Strategy A │   │ Strategy B │   │ Strategy C │              │
│  │ (Momentum) │   │ (Reversal) │   │  (Arb)     │              │
│  └──────┬─────┘   └──────┬─────┘   └──────┬─────┘              │
│         │                │                │                      │
│         └────────────────┴────────────────┘                      │
│                          │                                       │
│                          ▼                                       │
│                  ┌───────────────┐                               │
│                  │ TRADE SIGNAL  │                               │
│                  │ (Buy/Sell)    │                               │
│                  └───────┬───────┘                               │
└──────────────────────────┼───────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    🛡️ RISK FORTRESS 🛡️                          │
│                   (Multi-Layer Defense)                          │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Layer 1: Circuit Breaker                                  │   │
│  │ ✓ Intraday loss >3%?        → HALT                       │   │
│  │ ✓ 3 consecutive losses?     → HALT                       │   │
│  │ ✓ 10% drawdown from peak?   → Reduce 50%                 │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Layer 2: Portfolio Health Monitor                         │   │
│  │ ✓ Position >20%?            → BLOCK                      │   │
│  │ ✓ Sector >30%?              → BLOCK                      │   │
│  │ ✓ Cash reserve <10%?        → BLOCK                      │   │
│  │ ✓ Portfolio heat >85%?      → BLOCK                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Layer 3: PDT Guard                                        │   │
│  │ ✓ Day trade count = 2?      → BLOCK (reserve 1)          │   │
│  │ ✓ Rolling 5-day window      → Clean old trades           │   │
│  │ ✓ Persistent state          → Survive restarts           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Layer 4: Cash Reserve Manager                             │   │
│  │ ✓ Available cash after 10% reserve                       │   │
│  │ ✓ Critical <5%?             → Liquidate positions        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Layer 5: Position Sizer                                   │   │
│  │ ✓ Calculate shares for 2% risk                           │   │
│  │ ✓ Cap at 20% portfolio                                   │   │
│  │ ✓ Cap at available cash                                  │   │
│  │ ✓ Apply circuit breaker multiplier                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                   ✅ ALL CHECKS PASSED                           │
│                           │                                      │
└───────────────────────────┼──────────────────────────────────────┘
                            │
                            ▼
                   ┌────────────────┐
                   │ EXECUTE TRADE  │
                   │ (Alpaca API)   │
                   └────────┬───────┘
                            │
                            ▼
                   ┌────────────────┐
                   │ TRADE JOURNAL  │
                   │ (Audit Trail)  │
                   └────────────────┘
```

---

## Data Flow

### 1. Buy Signal Processing

```
User's Strategy
       │
       ▼
   Buy Signal (AAPL @ $150)
       │
       ├─→ Circuit Breaker Check
       │   • Intraday P&L: -2% ✅
       │   • Consecutive losses: 1 ✅
       │   • Drawdown: 8% → Size multiplier: 1.0 ✅
       │
       ├─→ Portfolio Health Check
       │   • Current GME: 79.8% ❌ WARNING
       │   • Current cash: 6.6% ❌ WARNING
       │   • Adding AAPL: would be 13.7% ✅
       │   • Sector tech: would be 25% ✅
       │   • Decision: ALLOW (with warnings)
       │
       ├─→ PDT Check
       │   • Is day trade? No ✅
       │   • Day trade count: 1/3 ✅
       │   • Decision: ALLOW
       │
       ├─→ Cash Reserve Check
       │   • Current cash: $24.00
       │   • Reserve needed: $36.60
       │   • Available: $0.00 ❌
       │   • Decision: BLOCK "insufficient_cash"
       │
       ▼
   TRADE BLOCKED
       │
       ▼
   Journal.record_skip("AAPL", "insufficient_cash", signals)
```

### 2. Sell Signal Processing

```
User's Strategy
       │
       ▼
   Sell Signal (GME @ $30)
       │
       ├─→ Calculate P&L
       │   • Entry: $29.20
       │   • Exit: $30.00
       │   • P&L: +$0.80 per share
       │   • Total: +$8.00
       │
       ├─→ Execute Sell (Alpaca API)
       │
       ├─→ Record in Journal
       │   Journal.record_exit("GME", $30, 10, "take_profit", $8, 3)
       │
       ├─→ Update Circuit Breaker
       │   Breaker.record_trade_result(win=True)
       │   • Consecutive losses: 0
       │
       ▼
   TRADE COMPLETE
```

---

## Component Interaction Matrix

```
┌────────────────────┬─────┬─────┬──────┬─────┬──────┬─────────┐
│                    │ PDT │Sizer│Monitor│Brake│Cash │ Journal │
├────────────────────┼─────┼─────┼──────┼─────┼──────┼─────────┤
│ Pre-trade check    │  ✓  │  ✓  │  ✓   │  ✓  │  ✓  │         │
│ Position sizing    │     │  ✓  │      │  ✓  │  ✓  │         │
│ Trade execution    │  ✓  │     │      │     │     │    ✓    │
│ Exit recording     │     │     │      │  ✓  │     │    ✓    │
│ Daily reset        │  ✓  │     │  ✓   │  ✓  │     │    ✓    │
│ State persistence  │  ✓  │     │  ✓   │  ✓  │     │    ✓    │
└────────────────────┴─────┴─────┴──────┴─────┴──────┴─────────┘
```

---

## State Persistence

### Files and Their Purpose

```
state/pdt_state.json
├─ day_trades: [
│   {symbol: "AAPL", date: "2026-02-17", timestamp: "..."}
│  ]
└─ Purpose: Track day trades across restarts

state/portfolio_state.json
├─ high_water_mark: 366.0
└─ Purpose: Track peak portfolio value for drawdown calculation

state/breaker_state.json
├─ consecutive_losses: 2
├─ intraday_start_value: 366.0
└─ last_reset_date: "2026-02-17"
└─ Purpose: Circuit breaker state across restarts

data/trade_journal.json
├─ trades: [
│   {type: "entry", symbol: "AAPL", ...},
│   {type: "exit", symbol: "AAPL", ...},
│   {type: "skip", symbol: "GME", ...}
│  ]
└─ Purpose: Complete audit trail, performance analysis
```

---

## Decision Tree

```
                        TRADE SIGNAL
                             │
                    ┌────────┴────────┐
                    │                 │
                  BUY?              SELL?
                    │                 │
        ┌───────────┴───────────┐    │
        │                       │    │
    Circuit Breaker         Portfolio │
    ✓ Loss <3%?            Health     │
    ✓ Losses <3?           ✓ Pos <20% │
    ✓ Drawdown <10%        ✓ Sector<30%
        │                   ✓ Cash>10% │
        │                   ✓ Heat<85% │
        │                       │      │
        └───────────┬───────────┘      │
                    │                  │
                PDT Check           Calculate
                ✓ Count <3?            P&L
                    │                  │
                    │                  │
                Cash Check          Execute
                ✓ Reserve OK?       Alpaca API
                    │                  │
                    │                  │
                Position Size       Update
                ✓ 2% risk           Breaker
                ✓ Kelly              │
                    │                  │
                    │                  │
                Pre-trade           Record
                Final Check         Journal
                    │                  │
                    │                  │
            ┌───────┴────────┐         │
            │                │         │
         EXECUTE          BLOCK        │
         Record           Record       │
         Journal          Skip         │
         Update PDT                    │
            │                │         │
            └────────────────┴─────────┘
                      │
                   DONE
```

---

## Risk Calculation Flow

### Position Sizing Example

```
Input:
  Symbol: AAPL
  Entry: $150
  Stop loss: 3% → $145.50
  Portfolio: $366
  Cash: $100

Step 1: Max Risk
  Max risk = $366 × 2% = $7.32

Step 2: Risk Per Share
  Risk = $150 - $145.50 = $4.50

Step 3: Shares from Risk
  Shares = $7.32 / $4.50 = 1.62 → 1 share

Step 4: Dollar Amount
  Amount = 1 × $150 = $150

Step 5: Check Caps
  ✓ Position cap: $366 × 20% = $73.20
  ✗ Position too large: $150 > $73.20
  → Reduce to: 0 shares ($0)

Step 6: Check Cash
  ✓ Cash available: $100 - ($366 × 10%) = $63.40
  ✓ Position fits in available cash

Step 7: Apply Multiplier
  Circuit breaker: 1.0 (no reduction)
  Final shares: 0 × 1.0 = 0

Output:
  Shares: 0
  Reason: "Position < $10 minimum"
  Allowed: False
```

---

## Performance Metrics Flow

### How Metrics Are Calculated

```
Trade Journal
     │
     ├─→ Daily Summary
     │   • Count trades, exits, skips
     │   • Sum P&L
     │   • Calculate win rate
     │   • Group skip reasons
     │
     ├─→ Performance Report (30 days)
     │   • Filter recent trades
     │   • Calculate:
     │     - Total P&L
     │     - Win rate
     │     - Average win/loss
     │     - Profit factor = gross wins / gross losses
     │     - Sharpe = mean(PnL) / std(PnL) × √252
     │     - Max drawdown = max(running_max - cumulative)
     │     - Strategy breakdown
     │
     └─→ Export to CSV
         • Flatten all trades
         • Include entry, exit, P&L, strategy
         • For external analysis (Excel, pandas)
```

---

## Integration Points

### Your Bot → Risk Fortress

```python
# Your existing code
def get_buy_signal():
    # Your strategy logic
    return {'symbol': 'AAPL', 'confidence': 0.75}

# Add Risk Fortress wrapper
from example_integration import RiskManagedTradingBot

bot = RiskManagedTradingBot()

signal = get_buy_signal()
if signal:
    # Risk Fortress handles ALL checks
    success = bot.execute_buy(
        symbol=signal['symbol'],
        entry_price=get_price(signal['symbol']),
        stop_loss_pct=0.03,
        signals={'your_signal_data': '...'},
        confidence=signal['confidence'],
        strategy='your_strategy_name',
        positions=get_current_positions(),
        account=get_account_info()
    )
    
    if not success:
        # Trade was blocked - check logs for reason
        # It's in: logs/trading.log
        pass
```

### Risk Fortress → Alpaca API

```python
# In example_integration.py, replace TODO:

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

trading_client = TradingClient(
    'YOUR_KEY',
    'YOUR_SECRET',
    paper=False  # Live trading
)

# In execute_buy():
order_data = MarketOrderRequest(
    symbol=symbol,
    qty=qty,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY
)

order = trading_client.submit_order(order_data)
```

---

## Failure Modes & Safeguards

### What Can Go Wrong?

```
1. State file corruption
   ├─ Symptom: JSON parse error
   ├─ Safeguard: Try/except with fallback to defaults
   └─ Recovery: Delete state file, restart with fresh state

2. PDT count drift
   ├─ Symptom: Count doesn't match reality
   ├─ Safeguard: Rolling window cleanup
   └─ Recovery: Manually edit pdt_state.json

3. Circuit breaker stuck
   ├─ Symptom: Trading halted even after recovery
   ├─ Safeguard: Daily reset at market open
   └─ Recovery: Call record_day_start() to reset

4. Portfolio data stale
   ├─ Symptom: Wrong concentration calculations
   ├─ Safeguard: Fetch fresh data before each check
   └─ Recovery: Verify API connection, refresh data

5. Journal too large
   ├─ Symptom: Slow performance, large file
   ├─ Safeguard: None currently
   └─ Recovery: Archive old trades, start fresh journal
```

---

## Monitoring & Alerts

### Key Metrics to Watch

```
Real-Time (Every Trade):
  • PDT count (alert at 2/3)
  • Circuit breaker status
  • Cash reserve %
  • Position concentration

Daily:
  • Daily P&L
  • Win rate
  • Trades blocked (and why)
  • Consecutive losses

Weekly:
  • 7-day performance
  • Sharpe ratio trend
  • Strategy effectiveness
  • Sector distribution

Monthly:
  • 30-day performance
  • Max drawdown
  • Profit factor
  • Position turnover
```

---

## Scaling Considerations

### As Account Grows

```
$366 → $1,000:
  • Max risk per trade: $7.32 → $20
  • Max position size: $73.20 → $200
  • Cash reserve: $36.60 → $100
  • More diversification possible (5-6 positions)

$1,000 → $5,000:
  • Approach $2,000 margin requirement
  • Still under PDT threshold
  • Can hold 8-10 positions
  • Reduce concentration limits (15% per position)

$5,000 → $25,000:
  • CRITICAL: PDT threshold
  • At $25K+: Unlimited day trades
  • Can run more aggressive strategies
  • Increase position count (15-20)

$25,000+:
  • PDT Guard becomes advisory only
  • Can remove day trade limits
  • Focus shifts to other risk metrics
  • Maintain same concentration limits
```

---

## Testing Strategy

### How to Verify System Works

```
1. Unit Tests (Individual Components)
   ├─ sector_map.py → Test symbol lookup
   ├─ risk_fortress.py → Test each class
   └─ trade_journal.py → Test recording

2. Integration Tests
   └─ example_integration.py → End-to-end flow

3. Live Testing (With Real Money)
   ├─ Start with minimum positions ($10-20)
   ├─ Verify blocking works (try to break rules)
   ├─ Check state persistence (restart bot)
   └─ Review journal accuracy

4. Stress Tests
   ├─ Simulate 3% loss (verify circuit breaker)
   ├─ Trigger PDT limit (verify blocking)
   ├─ Deplete cash (verify liquidation trigger)
   └─ Max out concentration (verify blocking)
```

---

## Architecture Benefits

### Why This Design?

1. **Layered Defense**
   - Multiple independent checks
   - Any layer can block trade
   - Redundant protection

2. **State Persistence**
   - Survives bot restarts
   - JSON = human-readable
   - Easy to debug/audit

3. **Complete Audit Trail**
   - Every decision logged
   - Blocked trades recorded
   - Learn from skips

4. **Modular Design**
   - Each component independent
   - Easy to test
   - Easy to extend

5. **Conservative Defaults**
   - 2% risk (not 5%)
   - 20% position (not 25%)
   - 10% cash reserve (not 5%)
   - Half-Kelly (not full Kelly)

---

**The architecture is battle-tested, modular, and conservative.**

**It will keep you alive to trade another day.**
