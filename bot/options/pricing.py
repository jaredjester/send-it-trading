"""Monte Carlo + Black-Scholes pricing engine with Greeks."""
# Consolidated pricing functions moved to engine/core/pricing.py
from engine.core.pricing import (
    OptionSpec, bs_price, bs_greeks, implied_vol,
    mc_price_gbm, mc_price_heston, pnl_distribution
)
