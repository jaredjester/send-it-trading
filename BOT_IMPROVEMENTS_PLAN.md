# Bot Improvements Plan: Daily High-ROI Opportunities

**Goal:** Find 5-10 explosive setups daily (10-30% potential) instead of 1-2 mediocre signals (3% potential)

**Timeline:** 7 days to deploy all improvements

---

## Phase 1: Morning Momentum (Days 1-2)

### 1.1 Gap & Go Scanner (Day 1 - TODAY)

**What:** Scan for stocks gapping up 5%+ pre-market with volume

**Implementation:**
```
morning_gap_scanner.py
├── Pre-market scan (7:00 AM - 9:30 AM)
├── Filter: Gap > 5%, Volume > 500K, Price $5-$500
├── Score: News catalyst + sector strength + relative volume
├── Entry: 9:35 AM (after initial volatility settles)
├── Exit: Trailing stop 5% OR 11:00 AM (whichever first)
```

**Expected Results:**
- 3-5 gap plays/day
- 60% win rate
- 10-20% avg gain on winners
- 2-3 losers stopped at -5%

**Deployment:**
- Build locally (2 hours)
- Test on historical data (1 hour)
- Deploy to Pi (30 min)
- Monitor for 24 hours

### 1.2 Pre-Market Data Integration (Day 2)

**What:** Get real pre-market prices (not just yesterday's close)

**Implementation:**
```
premarket_data.py
├── Alpaca IEX feed (pre-market bars)
├── Cache top 100 most active stocks
├── Update every 5 minutes (7:00-9:30 AM)
├── Calculate gap % from previous close
```

**Why:** Need actual pre-market prices to detect gaps accurately

---

## Phase 2: Catalyst Detection (Days 3-4)

### 2.1 Unusual Volume + News Filter (Day 3)

**What:** Detect volume spikes + check if there's a bullish catalyst

**Implementation:**
```
catalyst_scanner.py
├── Volume spike detector (3x+ average volume)
├── News API integration (Alpaca news feed)
├── Sentiment scoring (FDA approval, acquisition, earnings beat)
├── Urgency filter (news < 2 hours old = fresh catalyst)
├── Entry: If volume + bullish news + price breaking resistance
```

**Expected Results:**
- 1-3 catalyst plays/day
- 70% win rate (news-driven moves more predictable)
- 15-30% avg gain
- Fast moves (1-4 hours)

### 2.2 News Sentiment Scoring (Day 4)

**What:** Score news quality (not just sentiment)

**Implementation:**
```
news_scorer.py
├── Keyword extraction (FDA, acquisition, earnings, etc.)
├── Impact scoring (material vs noise)
├── Recency weighting (fresh news > old news)
├── Source credibility (WSJ > random blog)
```

---

## Phase 3: Breakout Detection (Days 5-6)

### 3.1 Real-Time Price Monitoring (Day 5)

**What:** WebSocket connection for instant breakout detection

**Implementation:**
```
realtime_monitor.py
├── Alpaca WebSocket (real-time bars)
├── Track top 50 momentum stocks
├── Detect: Price crosses resistance + volume confirmation
├── Alert orchestrator immediately (don't wait 30 min)
```

**Why:** Catch breakouts in first 5 minutes, not 30 minutes later

### 3.2 Technical Breakout Patterns (Day 6)

**What:** Detect high-probability chart patterns

**Implementation:**
```
pattern_detector.py
├── Bull flag detection (consolidation after move)
├── Cup & handle (breakout pattern)
├── Ascending triangle (squeeze into breakout)
├── 52-week high breakout (momentum confirmation)
```

**Expected Results:**
- 2-4 pattern setups/day
- 65% win rate
- 8-15% avg gain
- Multi-day holds (2-5 days)

---

## Phase 4: Integration & Optimization (Day 7)

### 4.1 Multi-Signal Orchestrator

**What:** Combine all scanners into priority system

**Implementation:**
```
opportunity_ranker.py
├── Input: Gap scanner, catalyst scanner, pattern scanner
├── Score each opportunity (0-100)
├── Rank by: Edge (IC) × Urgency × Conviction
├── Execute top 3 daily (portfolio heat management)
```

**Scoring:**
```python
score = (
    catalyst_strength * 30 +  # News-driven = high weight
    volume_spike * 25 +        # Unusual volume = strong signal
    technical_setup * 20 +     # Pattern confirmation
    sector_strength * 15 +     # Sector tailwind
    relative_strength * 10     # Outperforming market
)
```

### 4.2 Position Sizing by Edge

**What:** Size positions based on signal quality

**Implementation:**
```python
if score > 80:  # Strong catalyst + volume + pattern
    size = 25% of portfolio
elif score > 60:  # Good setup
    size = 15% of portfolio
elif score > 40:  # Marginal
    size = 5% of portfolio
else:
    skip
```

**Why:** Risk more when edge is proven, less when marginal

---

## Expected Improvement Metrics

### Before (Current):
```
Signals/day: 1-2
Avg gain: 3-5%
Win rate: 50%
Monthly return: +3-10%
```

### After (Improved):
```
Signals/day: 5-10
Avg gain: 10-20%
Win rate: 60-65%
Monthly return: +30-50%
```

### On $367 Portfolio:
```
Before: +$11-37/month
After: +$110-185/month

12 months compounded:
Before: $367 → $525 (+43%)
After: $367 → $1,467 (+300%)
```

---

## Daily Workflow (After Improvements)

**7:00 AM:** Pre-market scan starts
- Gap scanner identifies 10-15 candidates
- News filter checks catalysts
- Ranks by score

**9:30 AM:** Market opens
- Real-time monitor tracks breakouts
- Pattern detector checks setups
- Top 3 opportunities queued

**9:35 AM:** Execute top setups
- Enter gap plays (after initial volatility)
- Set trailing stops (5%)
- Log decisions

**10:00 AM - 3:00 PM:** Monitor + react
- WebSocket alerts on breakouts
- Catalyst scanner finds intraday news
- Execute opportunistically

**4:00 PM:** Close of day
- Review performance
- Update IC metrics
- Adjust scoring weights

---

## Risk Management

**Position limits:**
- Max 25% per position (strong setups only)
- Max 3 concurrent positions
- Max 75% portfolio heat

**Stop losses:**
- All positions: -5% hard stop
- Gap plays: Trailing stop 5% from high
- Catalyst plays: Exit if news invalidated

**Daily loss limit:**
- Max -3% portfolio/day
- Circuit breaker at -5%

---

## Deployment Plan

**Day 1 (Today):**
- [ ] Build gap_scanner.py
- [ ] Test on historical data
- [ ] Deploy to Pi
- [ ] Monitor first trades

**Day 2:**
- [ ] Add pre-market data
- [ ] Refine gap scoring
- [ ] Backtest on 30 days

**Day 3:**
- [ ] Build catalyst_scanner.py
- [ ] Integrate news API
- [ ] Test volume + news combinations

**Day 4:**
- [ ] Improve news scoring
- [ ] Add urgency filters
- [ ] Backtest catalyst plays

**Day 5:**
- [ ] WebSocket real-time feeds
- [ ] Breakout detection
- [ ] Test latency

**Day 6:**
- [ ] Pattern detector
- [ ] Multi-day setups
- [ ] Backtest patterns

**Day 7:**
- [ ] Integrate all scanners
- [ ] Opportunity ranking
- [ ] Full system test
- [ ] Deploy final version

---

## Success Metrics (30 Days)

**Measure:**
- [ ] Signals found/day (target: 5-10)
- [ ] Win rate (target: 60%+)
- [ ] Avg gain on winners (target: 10%+)
- [ ] Max drawdown (keep < 10%)
- [ ] Monthly return (target: 30%+)

**If metrics hit:**
- Document case studies
- Add to GitHub repo
- Market as "proven system"
- Sell as service/product

---

## Next Steps After 30 Days

**If successful (30%+ monthly):**
1. Raise capital (show track record)
2. Apply to prop trading firm
3. Sell system as service ($500/mo)
4. Build web dashboard for subscribers

**If mediocre (10-15% monthly):**
1. Continue refining
2. Focus on income streams instead
3. Use bot for personal trading only

---

**Starting execution NOW. Building gap scanner first.**
