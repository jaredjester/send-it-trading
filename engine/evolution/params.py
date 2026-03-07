"""
Parameter space for walk-forward evolution.

Defines what can be mutated between workers and the bounds for each param.
The optimizer samples from these ranges to generate new worker configs.
"""
from typing import Any

# Parameter space: name -> (type, min, max, step)
PARAM_SPACE: dict[str, tuple] = {
    # Alpha engine weights (must sum to ~1.0 — optimizer normalizes)
    "momentum.score_weight":       ("float", 0.30, 0.80, 0.05),
    "mean_reversion.score_weight": ("float", 0.10, 0.45, 0.05),
    "sentiment.score_weight":      ("float", 0.05, 0.30, 0.05),

    # Scoring threshold — below this score = skip trade
    "min_score_threshold":         ("int",   30,   65,   5),

    # Options execution params
    "options.max_premium":         ("float", 0.75, 2.50, 0.25),
    "options.stop_loss_pct":       ("float", 0.30, 0.70, 0.05),
    "options.take_profit_pct":     ("float", 0.75, 1.50, 0.25),
    "options.expiry_min_days":     ("int",   7,    21,   7),
    "options.expiry_max_days":     ("int",   21,   45,   7),
}

# Named presets — starting points for worker containers
PRESETS: dict[str, dict[str, Any]] = {
    "aggressive": {
        "momentum.score_weight": 0.70,
        "mean_reversion.score_weight": 0.20,
        "sentiment.score_weight": 0.10,
        "min_score_threshold": 38,
        "options.max_premium": 2.00,
        "options.stop_loss_pct": 0.60,
        "options.take_profit_pct": 1.00,
    },
    "balanced": {
        "momentum.score_weight": 0.55,
        "mean_reversion.score_weight": 0.35,
        "sentiment.score_weight": 0.20,
        "min_score_threshold": 45,
        "options.max_premium": 1.50,
        "options.stop_loss_pct": 0.50,
        "options.take_profit_pct": 1.00,
    },
    "conservative": {
        "momentum.score_weight": 0.40,
        "mean_reversion.score_weight": 0.40,
        "sentiment.score_weight": 0.20,
        "min_score_threshold": 55,
        "options.max_premium": 1.00,
        "options.stop_loss_pct": 0.40,
        "options.take_profit_pct": 0.75,
    },
    "momentum": {
        "momentum.score_weight": 0.80,
        "mean_reversion.score_weight": 0.10,
        "sentiment.score_weight": 0.10,
        "min_score_threshold": 40,
        "options.max_premium": 2.00,
        "options.stop_loss_pct": 0.55,
        "options.take_profit_pct": 1.25,
    },
}


def mutate(parent: dict[str, Any], mutation_rate: float = 0.3) -> dict[str, Any]:
    """
    Create a child config by randomly mutating a parent.
    Used to generate new workers after each evolution cycle.
    """
    import random
    child = parent.copy()
    for param, (ptype, lo, hi, step) in PARAM_SPACE.items():
        if random.random() < mutation_rate:
            if ptype == "float":
                steps = round((hi - lo) / step)
                child[param] = round(lo + random.randint(0, steps) * step, 4)
            else:
                steps = (hi - lo) // step
                child[param] = lo + random.randint(0, steps) * step
    # Normalize alpha weights to sum to 1.0
    weight_keys = [k for k in child if k.endswith(".score_weight")]
    total = sum(child[k] for k in weight_keys)
    if total > 0:
        for k in weight_keys:
            child[k] = round(child[k] / total, 4)
    return child


def crossover(a: dict, b: dict) -> dict:
    """Combine two parent configs, picking each param from the better-performing one."""
    import random
    return {k: (a[k] if random.random() > 0.5 else b[k]) for k in a}
