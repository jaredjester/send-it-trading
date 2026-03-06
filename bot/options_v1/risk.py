"""Portfolio-level Greek risk manager + stress tests."""
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class GreekLimits:
    delta: float = 50.0
    gamma: float = 20.0
    vega:  float = 500.0
    theta: float = -200.0   # max daily decay


@dataclass
class Position:
    symbol: str
    kind: str
    strike: float
    expiry: float
    quantity: int
    delta: float
    gamma: float
    vega: float
    theta: float
    entry_price: float


@dataclass
class PortfolioRisk:
    net_delta: float
    net_gamma: float
    net_vega:  float
    net_theta: float
    stress_loss_10pct: float   # estimated loss on 10% underlying drop
    stress_loss_20pct: float
    within_limits: bool
    breach_reasons: List[str] = field(default_factory=list)


class RiskManager:
    def __init__(self, limits: GreekLimits = None):
        self.limits    = limits or GreekLimits()
        self.positions: List[Position] = []

    def add_position(self, pos: Position):
        self.positions.append(pos)

    def remove_position(self, symbol: str, strike: float, kind: str):
        self.positions = [
            p for p in self.positions
            if not (p.symbol == symbol and p.strike == strike and p.kind == kind)
        ]

    def portfolio_greeks(self) -> Dict[str, float]:
        g = {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'theta': 0.0}
        for p in self.positions:
            mult = p.quantity * 100
            g['delta'] += p.delta * mult
            g['gamma'] += p.gamma * mult
            g['vega']  += p.vega  * mult
            g['theta'] += p.theta * mult
        return g

    def stress_test(self, spot: float, shock_pct: float) -> float:
        """Estimate portfolio PnL under a spot price shock."""
        dS  = spot * shock_pct
        g   = self.portfolio_greeks()
        pnl = g['delta'] * dS + 0.5 * g['gamma'] * dS**2
        return pnl

    def evaluate(self, spot: float) -> PortfolioRisk:
        g = self.portfolio_greeks()
        breaches = []
        if abs(g['delta']) > self.limits.delta:
            breaches.append(f'delta {g["delta"]:.1f} exceeds ±{self.limits.delta}')
        if abs(g['gamma']) > self.limits.gamma:
            breaches.append(f'gamma {g["gamma"]:.1f} exceeds ±{self.limits.gamma}')
        if abs(g['vega']) > self.limits.vega:
            breaches.append(f'vega {g["vega"]:.1f} exceeds ±{self.limits.vega}')
        if g['theta'] < self.limits.theta:
            breaches.append(f'theta {g["theta"]:.1f} below {self.limits.theta}')

        return PortfolioRisk(
            net_delta=g['delta'],
            net_gamma=g['gamma'],
            net_vega=g['vega'],
            net_theta=g['theta'],
            stress_loss_10pct=self.stress_test(spot, -0.10),
            stress_loss_20pct=self.stress_test(spot, -0.20),
            within_limits=len(breaches) == 0,
            breach_reasons=breaches,
        )

    def scale_for_limits(self, proposed_delta: float, proposed_gamma: float,
                          proposed_vega: float, proposed_contracts: int) -> int:
        """Clip contracts so adding the trade keeps Greeks in bounds."""
        g = self.portfolio_greeks()
        scales = [1.0]
        if proposed_delta != 0:
            room = max(0, self.limits.delta - abs(g['delta']))
            scales.append(room / (abs(proposed_delta) * 100 + 1e-9))
        if proposed_gamma != 0:
            room = max(0, self.limits.gamma - abs(g['gamma']))
            scales.append(room / (abs(proposed_gamma) * 100 + 1e-9))
        if proposed_vega != 0:
            room = max(0, self.limits.vega - abs(g['vega']))
            scales.append(room / (abs(proposed_vega) * 100 + 1e-9))
        scale = min(scales)
        return max(0, int(proposed_contracts * scale))
