from dataclasses import dataclass
from typing import Protocol, Dict, Any
import numpy as np

class PriceModel(Protocol):
    def simulate(self, spot: float, horizon: float, n_paths: int) -> np.ndarray:
        ...

class VolModel(Protocol):
    def simulate(self, iv: float, horizon: float, n_paths: int) -> np.ndarray:
        ...

@dataclass
class StateModelConfig:
    price_model: PriceModel
    vol_model: VolModel
    correlation: float = 0.0

class GBMPriceModel:
    def __init__(self, mu: float, sigma: float):
        self.mu = mu
        self.sigma = sigma

    def simulate(self, spot: float, horizon: float, n_paths: int) -> np.ndarray:
        dt = horizon
        shocks = np.random.normal(0, np.sqrt(dt), size=n_paths)
        return spot * np.exp((self.mu - 0.5 * self.sigma**2) * dt + self.sigma * shocks)

class MeanRevertingVolModel:
    def __init__(self, kappa: float, theta: float, eta: float):
        self.kappa = kappa
        self.theta = theta
        self.eta = eta

    def simulate(self, iv: float, horizon: float, n_paths: int) -> np.ndarray:
        dt = horizon
        shocks = np.random.normal(0, np.sqrt(dt), size=n_paths)
        return iv + self.kappa * (self.theta - iv) * dt + self.eta * shocks
