"""Trade planner — creates and tracks structured trade plans with targets and stops."""
import json
import os
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

PLANS_PATH = Path(str(Path(os.getenv('DATA_DIR', str(Path(__file__).resolve().parent.parent.parent / 'data'))) / 'bot_trade_plans.jsonl'))


@dataclass
class TradePlan:
    plan_id: str            # same as trade_id
    trade_id: str
    symbol: str             # underlying (UBER)
    occ_symbol: str         # UBER260410C00080000
    strategy: str           # DCVX
    direction: str          # call | put

    # Entry
    entry_price: float      # what we paid (option price)
    entry_ts: str           # ISO timestamp
    entry_thesis: str       # human-readable
    spot_at_entry: float    # underlying spot price at entry

    # Targets (pre-planned before entry)
    target_price: float     # option price take-profit
    stop_price: float       # option price stop-loss
    target_date: str        # deadline
    catalyst_window_days: int
    risk_reward: float      # target_gain / max_loss

    # P&L targets in dollars
    max_loss_dollars: float
    target_gain_dollars: float

    # Contracts
    contracts: int = 1

    # Status
    status: str = 'open'    # open | target_hit | stop_hit | expired | switched | manual_close | oc_switch
    exit_price: Optional[float] = None
    exit_ts: Optional[str] = None
    exit_reason: Optional[str] = None
    actual_pnl: Optional[float] = None
    target_hit: Optional[bool] = None
    stop_hit: Optional[bool] = None

    # RL context (signals at entry)
    news_score: float = 0.0
    insider_score: float = 0.0
    ca_score: float = 0.0
    polymarket_score: float = 0.0
    ev_at_entry: float = 0.0
    kelly_at_entry: float = 0.0
    rl_kelly_scale: float = 1.0

    # OC tracking
    oc_checks: int = 0
    oc_switch_offered: bool = False
    oc_switch_taken: bool = False
    actual_rr: float = None
    oc_last_ev_hold: float = None
    oc_last_ev_new: float = None


def create_plan(sig, entry_price: float, contracts: int,
                news_score: float, insider_score: float,
                ca_score: float, polymarket_score: float,
                rl_kelly_scale: float, trade_id: str,
                occ_symbol: str) -> TradePlan:
    """Create a structured trade plan before entry."""
    now = datetime.now()

    stop_price = round(entry_price * 0.50, 2)
    target_price = round(entry_price + (entry_price - stop_price) * 2.0, 2)

    # Catalyst window
    expiry_days = max(1, int(getattr(sig, 'expiry_years', 0.1) * 365))
    if news_score > 0.5:
        catalyst_window_days = 5
    elif insider_score > 0.3:
        catalyst_window_days = 14
    else:
        catalyst_window_days = 7
    catalyst_window_days = min(catalyst_window_days, int(expiry_days * 0.6))
    catalyst_window_days = max(1, catalyst_window_days)

    target_date = (now + timedelta(days=catalyst_window_days)).isoformat()

    entry_thesis = (
        f"{sig.symbol} {sig.kind.upper()} | "
        f"news={news_score:.2f} insider={insider_score:.2f} poly={polymarket_score:.2f} | "
        f"EV={sig.ev:.1f} | {catalyst_window_days}d window"
    )

    max_loss_dollars = (entry_price - stop_price) * 100 * contracts
    target_gain_dollars = (target_price - entry_price) * 100 * contracts
    risk_reward = round(target_gain_dollars / max(max_loss_dollars, 0.01), 2)

    return TradePlan(
        plan_id=trade_id,
        trade_id=trade_id,
        symbol=sig.symbol,
        occ_symbol=occ_symbol,
        strategy=sig.strategy,
        direction=sig.kind,
        entry_price=entry_price,
        entry_ts=now.isoformat(),
        entry_thesis=entry_thesis,
        spot_at_entry=getattr(sig, 'spot', 0.0),
        target_price=target_price,
        stop_price=stop_price,
        target_date=target_date,
        catalyst_window_days=catalyst_window_days,
        risk_reward=risk_reward,
        max_loss_dollars=max_loss_dollars,
        target_gain_dollars=target_gain_dollars,
        contracts=contracts,
        news_score=news_score,
        insider_score=insider_score,
        ca_score=ca_score,
        polymarket_score=polymarket_score,
        ev_at_entry=sig.ev,
        kelly_at_entry=sig.kelly_fraction,
        rl_kelly_scale=rl_kelly_scale,
    )


def save_plan(plan: TradePlan):
    """Append a trade plan to the JSONL file."""
    PLANS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLANS_PATH, 'a') as f:
        f.write(json.dumps(asdict(plan)) + '\n')
    logger.info('[PLAN] Saved plan %s for %s %s', plan.plan_id[:8], plan.symbol, plan.direction)


def load_open_plans() -> List[TradePlan]:
    """Read all plans where status == 'open'."""
    if not PLANS_PATH.exists():
        return []
    plans = []
    for line in PLANS_PATH.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if d.get('status') == 'open':
                plans.append(TradePlan(**d))
        except Exception as e:
            logger.warning('[PLAN] Failed to parse plan line: %s', e)
    return plans


def close_plan(plan_id: str, exit_price: float, exit_reason: str, contracts: int = None) -> Optional[TradePlan]:
    """Atomically update a plan's status and exit fields."""
    if not PLANS_PATH.exists():
        return None
    records = []
    closed_plan = None
    for line in PLANS_PATH.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if d.get('plan_id') == plan_id and d.get('status') == 'open':
                d['status'] = exit_reason
                d['exit_price'] = exit_price
                d['exit_ts'] = datetime.now().isoformat()
                d['exit_reason'] = exit_reason
                entry = d.get('entry_price', 0)
                n_contracts = d.get('contracts', 1)
                d['actual_pnl'] = round((exit_price - entry) * 100 * n_contracts, 2)
                d['target_hit'] = exit_reason == 'target_hit'
                d['stop_hit'] = exit_reason == 'stop_hit'
                try:
                    closed_plan = TradePlan(**d)
                except Exception:
                    pass
            records.append(d)
        except Exception as e:
            logger.warning('[PLAN] Error processing plan line: %s', e)

    # Atomic write — write to .tmp then rename
    tmp = PLANS_PATH.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')
    tmp.replace(PLANS_PATH)
    return closed_plan


def get_plan_summary() -> dict:
    """Return aggregate stats across all plans."""
    if not PLANS_PATH.exists():
        return {}
    all_plans = []
    for line in PLANS_PATH.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            all_plans.append(json.loads(line))
        except Exception:
            continue

    closed = [p for p in all_plans if p.get('status') != 'open']
    open_p = [p for p in all_plans if p.get('status') == 'open']
    targets_hit = sum(1 for p in closed if p.get('target_hit'))
    stops_hit = sum(1 for p in closed if p.get('stop_hit'))
    pnls = [p['actual_pnl'] for p in closed if p.get('actual_pnl') is not None]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

    # Window accuracy: ratio of days_held to catalyst_window
    accuracies = []
    for p in closed:
        if p.get('entry_ts') and p.get('exit_ts') and p.get('catalyst_window_days'):
            try:
                entry_dt = datetime.fromisoformat(p['entry_ts'])
                exit_dt = datetime.fromisoformat(p['exit_ts'])
                days_held = max(1, (exit_dt - entry_dt).days)
                ratio = days_held / p['catalyst_window_days']
                accuracies.append(min(ratio, 1.0))
            except Exception:
                pass

    return {
        'total_plans': len(all_plans),
        'open': len(open_p),
        'closed': len(closed),
        'targets_hit': targets_hit,
        'stops_hit': stops_hit,
        'win_rate': f"{targets_hit}/{len(closed)}" if closed else "0/0",
        'avg_pnl': round(avg_pnl, 2),
        'window_accuracy': round(sum(accuracies) / len(accuracies), 2) if accuracies else 0.0,
    }


def update_plan_oc(plan_id: str, oc_checks: int = None,
                   oc_switch_offered: bool = None,
                   oc_ev_hold: float = None,
                   oc_ev_new: float = None):
    """Atomically update OC tracking fields on an existing plan."""
    if not PLANS_PATH.exists():
        return
    records = []
    for line in PLANS_PATH.read_text().strip().split('\n'):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if d.get('plan_id') == plan_id:
                if oc_checks is not None:
                    d['oc_checks'] = oc_checks
                if oc_switch_offered is not None:
                    d['oc_switch_offered'] = oc_switch_offered
                if oc_ev_hold is not None:
                    d['oc_last_ev_hold'] = round(oc_ev_hold, 2)
                if oc_ev_new is not None:
                    d['oc_last_ev_new'] = round(oc_ev_new, 2)
            records.append(d)
        except Exception as e:
            logger.warning('[PLAN] update_plan_oc parse error: %s', e)
    tmp = PLANS_PATH.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        for rec in records:
            f.write(json.dumps(rec) + '\n')
    tmp.replace(PLANS_PATH)
