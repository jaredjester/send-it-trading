"""Opportunity Cost Engine — evaluates whether to hold a losing position or switch."""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class OpportunityCostEngine:
    SWITCH_MULTIPLIER = 1.5   # new EV must be 1.5× better than hold EV to trigger switch
    MAX_LOSS_HOLD = 0.65      # if down >65%, don't hold — almost certainly dead
    MIN_DTE_HOLD = 7          # if less than 7 DTE remaining, never hold a loser

    def ev_of_holding(self, plan, current_option_price: float,
                      current_delta: float, days_remaining: int) -> float:
        """
        Estimate remaining expected value of holding current losing position.

        P(recovery) = max(0.05, current_delta)
            — delta is a natural proxy for P(ITM at expiry)
            — floored at 5% so options with tiny DTE still have some value

        Adjusted down if:
          - We're past 50% of catalyst window (thesis timing broken)
          - Loss > 40% (momentum against us)

        EV(hold) = P(recovery) * target_gain_dollars
                 - (1 - P(recovery)) * current_value_dollars
                 - theta_burn_remaining (simplified: 20% of current_value per week)
        """
        contracts = plan.contracts
        current_value = current_option_price * 100 * contracts

        p_recovery = max(0.05, current_delta)

        # Penalize if we're past the catalyst window
        now = datetime.now()
        target_dt = datetime.fromisoformat(plan.target_date)
        days_to_deadline = max(0, (target_dt - now).days)
        deadline_fraction = days_to_deadline / max(1, plan.catalyst_window_days)
        if deadline_fraction < 0.3:  # less than 30% of window left
            p_recovery *= 0.5  # halve recovery odds — thesis timing broken

        # Penalize if deep loss
        loss_pct = (plan.entry_price - current_option_price) / max(plan.entry_price, 0.01)
        if loss_pct > 0.4:
            p_recovery *= (1 - (loss_pct - 0.4) * 2)  # ramp down as loss deepens
        p_recovery = max(0.03, p_recovery)

        # Theta burn estimate (rough: 20% of current value per 7 days)
        theta_burn = current_value * 0.20 * (days_remaining / 7)

        ev_hold = (p_recovery * plan.target_gain_dollars
                   - (1 - p_recovery) * current_value
                   - theta_burn)

        return ev_hold

    def evaluate(self, plan, current_option_price: float,
                 current_delta: float, days_remaining: int,
                 new_signals: list, freed_capital: float) -> dict:
        """
        Returns evaluation dict:
        {
          'should_switch': bool,
          'should_exit': bool,
          'switch_score': float,
          'ev_hold': float,
          'best_signal': Signal or None,
          'ev_new': float,
          'reason': str,
          'force_reason': str or None,
        }
        """
        # Force exit conditions (no new signal needed)
        loss_pct = (plan.entry_price - current_option_price) / max(plan.entry_price, 0.01)

        if loss_pct >= self.MAX_LOSS_HOLD:
            return {
                'should_switch': False, 'should_exit': True,
                'reason': f'Force exit: down {loss_pct*100:.0f}% exceeds {self.MAX_LOSS_HOLD*100:.0f}% max',
                'force_reason': 'max_loss_exceeded', 'ev_hold': -999, 'ev_new': 0,
                'switch_score': 0, 'best_signal': None,
            }
        if days_remaining <= self.MIN_DTE_HOLD:
            return {
                'should_switch': False, 'should_exit': True,
                'reason': f'Force exit: only {days_remaining} DTE remaining',
                'force_reason': 'near_expiry', 'ev_hold': -999, 'ev_new': 0,
                'switch_score': 0, 'best_signal': None,
            }

        ev_hold = self.ev_of_holding(plan, current_option_price, current_delta, days_remaining)

        # Find best approved new signal
        approved = [s for s in new_signals if s.action != 'skip']
        if not approved:
            return {
                'should_switch': False, 'should_exit': False,
                'reason': 'No approved signals to switch to',
                'force_reason': None, 'ev_hold': ev_hold, 'ev_new': 0,
                'switch_score': 0, 'best_signal': None,
            }

        # EV of new signal = signal.ev * freed_capital (simplified)
        best = max(approved, key=lambda s: s.ev)
        ev_new = best.ev * freed_capital / 100  # rough $ value

        switch_score = ev_new / max(ev_hold, 0.01) if ev_hold > 0 else 99.0
        should_switch = switch_score >= self.SWITCH_MULTIPLIER

        reason = (
            f"Switch score={switch_score:.2f}x | "
            f"{best.symbol} {best.kind.upper()} EV=${ev_new:.0f} vs "
            f"hold {plan.symbol} EV=${ev_hold:.0f} | "
            f"{'SWITCH' if should_switch else 'hold'}"
        )

        return {
            'should_switch': should_switch,
            'should_exit': should_switch,
            'switch_score': switch_score,
            'ev_hold': round(ev_hold, 2),
            'ev_new': round(ev_new, 2),
            'best_signal': best,
            'reason': reason,
            'force_reason': None,
        }
