from dataclasses import dataclass
import numpy as np

@dataclass
class OptionContract:
    kind: str  # 'call' or 'put'
    strike: float
    expiry: float  # years
    iv: float
    quantity: int

@dataclass
class PnLResult:
    pnl_paths: np.ndarray
    expected_value: float

class PnLEngine:
    def __init__(self, pricing_model):
        self.pricing_model = pricing_model

    def monte_carlo(self, option: OptionContract, spots: np.ndarray, vols: np.ndarray, transaction_cost: float = 0.0) -> PnLResult:
        terminal_values = self.pricing_model(option, spots, vols)
        pnl_paths = terminal_values - self.pricing_model(option, np.array([spots[0]]), np.array([vols[0]])) - transaction_cost
        return PnLResult(pnl_paths=pnl_paths, expected_value=float(np.mean(pnl_paths)))

    def greek_approx(self, delta: float, gamma: float, vega: float, theta: float, dS: np.ndarray, dVol: np.ndarray, dt: float) -> PnLResult:
        pnl_paths = delta * dS + 0.5 * gamma * (dS ** 2) + vega * dVol + theta * dt
        return PnLResult(pnl_paths=pnl_paths, expected_value=float(np.mean(pnl_paths)))
