# defaults.py - Default configuration values for the trading system
# These are used when live_config.json doesn't have a value

DEFAULTS = {
    # Trading thresholds (overwritten by overnight_optimizer.py)
    "min_score_threshold":  63.0,
    "max_position_pct":     0.12,
    "stop_loss_pct":       -0.06,
    "live_sharpe_haircut":  0.55,
    "max_total_exposure":   0.85,
    "min_cash_reserve":    75.0,
    "min_trade_notional":  10.0,
    "max_trades_per_cycle": 3,
    "min_position_value":   1.0,

    # IC / signal quality (overwritten by alpha_tracker)
    "ic_kill_threshold":    0.03,
    "ic_strong_threshold":  0.15,

    # Zombie cleanup
    "zombie_loss_threshold": -0.50,

    # Untradeable symbols (delisted / frozen)
    "untradeable_symbols": ["AVGR", "BGXXQ", "MOTS"],

    # Fallback watchlist when all scanners return empty
    "watchlist": [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA",
        "AMZN", "META", "AMD", "COIN", "MARA", "GOOGL",
    ],

    # RL episode outputs — written by episode_bridge after each trading day.
    "rl_action":              "hold",
    "rl_trade_multiplier":     1.0,
    "rl_size_multiplier":      1.0,
    "rl_last_episode_return":  0.0,
    "rl_updated_at":           None,

    # RL threshold bandit — structure of Thompson Sampling exploration
    "rl_default_threshold":    45,
    "rl_threshold_buckets":    [25, 30, 35, 40, 45, 50, 55, 60, 65, 70],

    # Options contract filters (used by options_trader.py)
    "options.max_premium":         1.50,   # max premium per share ($150/contract)
    "options.min_open_interest":   10,     # minimum OI for liquidity
    "options.min_expiry_days":     14,     # earliest expiry considered
    "options.max_expiry_days":     35,     # latest expiry considered
    "options.stop_loss_pct":       0.50,   # exit when position down this fraction
    "options.take_profit_pct":     1.00,   # exit when position up this fraction (doubles)
    "options.expiry_guard_days":   3,      # close any contract within N days of expiry

    # Position sizing formula (tunable by overnight_optimizer)
    "min_position_pct":       0.04,   # base position size
    "position_scale_factor":  0.06,   # how much position grows per score unit above threshold
    "position_floor_pct":     0.02,   # absolute minimum position fraction

    # Finviz scanner (initial signal scores before alpha engine re-scores)
    "finviz.max_per_screen":       8,
    "finviz.multi_screen_boost":   3,
    "finviz.score_momentum":      66,
    "finviz.score_oversold":      64,
    "finviz.score_breakout":      67,
    "finviz.score_insider":       65,
    "finviz.score_preearnings":   70,
    "finviz.score_postearnings":  69,
    "finviz.score_relstrength":   66,
    # ── Alpha Engine parameters (unified from master_config.json) ─────────────
    "alpha.mean_reversion.enabled": True,
    "alpha.mean_reversion.lookback_days": 20,
    "alpha.mean_reversion.rsi_oversold": 35,
    "alpha.mean_reversion.rsi_overbought": 65,
    "alpha.mean_reversion.std_dev_threshold": 1.5,
    "alpha.mean_reversion.volume_spike_min": 1.5,
    "alpha.mean_reversion.rsi_period": 14,
    "alpha.mean_reversion.target_hold_days": 5,
    "alpha.mean_reversion.score_weight": 0.35,
    "alpha.momentum.enabled": True,
    "alpha.momentum.sma_short": 20,
    "alpha.momentum.sma_long": 50,
    "alpha.momentum.adx_threshold": 25,
    "alpha.momentum.volume_growth_min": 0.1,
    "alpha.momentum.target_hold_days": 10,
    "alpha.momentum.score_weight": 0.55,
    "alpha.sentiment.enabled": True,
    "alpha.sentiment.positive_threshold": 0.2,
    "alpha.sentiment.negative_threshold": -0.1,
    "alpha.sentiment.score_weight": 0.10,
}