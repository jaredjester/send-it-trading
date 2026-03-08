"""Generalized Kelly optimizer + EV filter."""
import numpy as np
from scipy.optimize import minimize_scalar
from dataclasses import dataclass

@dataclass
class KellyResult:
    full_kelly: float
    fractional_kelly: float
    expected_value: float
    win_rate: float
    sharpe: float
    approved: bool       # passes EV filter


MIN_EV       = 0.0    # reject any trade with negative EV
MIN_WIN_RATE = 0.25   # 25% floor — OTM options have asymmetric payoff
FRACTIONAL   = 0.25   # trade 25% of full Kelly (conservative)


def compute_kelly(pnl_paths: np.ndarray, capital: float,
                  fractional: float = FRACTIONAL) -> KellyResult:
    """Compute generalized Kelly fraction from a PnL distribution."""
    r = pnl_paths / max(capital, 1.0)

    def neg_log_growth(f):
        inside = 1.0 + f * r
        if np.any(inside <= 0):
            return 1e9
        return -np.mean(np.log(inside))

    result = minimize_scalar(neg_log_growth, bounds=(0.0, 1.0), method='bounded')
    full_k = float(np.clip(result.x, 0.0, 1.0))
    frac_k = full_k * fractional

    ev       = float(np.mean(pnl_paths))
    win_rate = float(np.mean(pnl_paths > 0))
    std      = float(np.std(pnl_paths)) or 1e-9
    sharpe   = ev / std * np.sqrt(252)

    approved = (ev > MIN_EV) and (win_rate >= MIN_WIN_RATE)

    return KellyResult(
        full_kelly=full_k,
        fractional_kelly=frac_k,
        expected_value=ev,
        win_rate=win_rate,
        sharpe=sharpe,
        approved=approved,
    )


def position_size(capital: float, kelly_result: KellyResult,
                  option_price: float, lot: int = 100,
                  max_contracts: int = 2) -> int:
    """Return number of contracts to trade.
    Always at least 1 contract when Kelly approves — floor prevents $0 sizing
    on small accounts where dollar_risk < option_price * lot.
    Caps at max_contracts (default 2) to limit risk on small accounts.
    """
    if not kelly_result.approved or option_price <= 0:
        return 0
    dollar_risk = capital * kelly_result.fractional_kelly
    contracts   = int(dollar_risk / (option_price * lot))
    # Floor: 1 contract minimum when Kelly says go, cap at max
    contracts = max(1, contracts)
    contracts = min(contracts, max_contracts)
    return contracts
