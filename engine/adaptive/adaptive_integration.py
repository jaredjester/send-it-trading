"""
Adaptive Integration — Drop-in module for the stockbot scheduler.

Every 30 minutes this runs one STEP in the current day's EPISODE:
  1. Sync trades with Alpaca
  2. Update Bayesian signal beliefs from closed trades
  3. Detect market regime
  4. Ask Q-learner for recommended action (based on learned policy)
  5. Record episode step: (state, action, reward)
  6. At market close: end episode, compute Monte Carlo returns, train Q-learner

NO TRADES ARE PLACED — scoring and recommendations only.
"""

import json
import os
import sys
import logging
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from trade_tracker import TradeTracker
from adaptive_engine import AdaptiveEngine
from regime_detector import RegimeDetector
from episode_manager import EpisodeManager, discretize_state
from q_learner import QLearner

logger = logging.getLogger("adaptive.integration")

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "adaptive_config.json")
REPORT_INTERVAL = 6 * 3600
_last_report = 0


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


def run_adaptive_cycle(send_telegram=None):
    """
    Main entry point — called every 30 minutes from stockbot scheduler.

    Each call is a STEP within the current day's EPISODE.
    At market close the episode ends and Q-values are updated
    via Monte Carlo returns over the full day's trajectory.
    """
    global _last_report

    config = load_config()
    tracker = TradeTracker(
        config=config,
        trade_log_path=os.path.join(BASE_DIR, "trade_log.json")
    )
    engine = AdaptiveEngine(
        config=config,
        db_path=os.path.join(BASE_DIR, "learning_db.json"),
        history_path=os.path.join(BASE_DIR, "weight_history.json")
    )
    regime_detector = RegimeDetector(config=config)
    episode_mgr = EpisodeManager(config=config)
    q_learner = QLearner(config=config)

    # ── 1. Sync trades with Alpaca ──
    logger.info("Adaptive cycle: syncing with Alpaca...")
    newly_closed = tracker.sync_with_alpaca()
    trades_this_step = len(newly_closed)

    if newly_closed:
        logger.info(f"Found {trades_this_step} newly closed trades")
        engine.update_from_batch(newly_closed)
        if send_telegram:
            for trade in newly_closed:
                pnl = trade.get("pnl_pct", 0)
                symbol = trade.get("symbol", "?")
                if abs(pnl) >= 3:
                    emoji = "\U0001f4c8" if pnl > 0 else "\U0001f4c9"
                    send_telegram(
                        f"{emoji} {symbol}: {pnl:+.1f}% | "
                        f"Signals learned from this trade"
                    )
    else:
        logger.info("No newly closed trades")

    # ── 2. Detect regime ──
    detected_regime = "unknown"
    try:
        regime_result = regime_detector.detect("SPY")
        detected_regime = regime_result.get("regime", "unknown")
        engine.db["regime"]["current"] = detected_regime
        engine.db["regime"]["last_detected"] = datetime.utcnow().isoformat() + "Z"
        engine._save_db()
        logger.info(f"Market regime: {detected_regime}")
    except Exception as e:
        logger.error(f"Regime detection failed: {e}")

    # ── 3. Portfolio snapshot ──
    positions = tracker.get_current_positions()
    account = tracker._api_get("/v2/account") or {}

    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    equity = float(account.get("equity", 0))
    portfolio_heat = max(0, min(1, (equity - cash) / equity)) if equity > 0 else 0

    # ── 4. Q-learner recommends action ──
    intraday_pnl_pct = 0
    if episode_mgr.current_episode and episode_mgr.current_episode.initial_value > 0:
        intraday_pnl_pct = (
            (portfolio_value - episode_mgr.current_episode.initial_value)
            / episode_mgr.current_episode.initial_value
        )

    et_hour = (datetime.utcnow().hour - 5) % 24
    current_state = discretize_state(
        detected_regime, portfolio_heat, intraday_pnl_pct, et_hour
    )

    rl_action, was_exploration = q_learner.select_action(current_state)
    q_rec = q_learner.get_recommended_action(current_state)

    logger.info(
        f"RL action: {rl_action} "
        f"({'explore' if was_exploration else 'exploit'}) | "
        f"Q={q_rec['q_value']:.3f} | "
        f"Confidence={q_rec['confidence']:.2f}"
    )

    # ── 5. Episode step / lifecycle ──
    signal_snapshot = {}
    for sig_name, sig_data in engine.db.get("signals", {}).items():
        signal_snapshot[sig_name] = {
            "weight": sig_data.get("weight", 0),
            "accuracy": round(
                sig_data["alpha"] / (sig_data["alpha"] + sig_data["beta"]), 4
            )
        }

    step_result = episode_mgr.check_and_manage_episode(
        portfolio_value=portfolio_value,
        cash=cash,
        positions_count=len(positions),
        regime=detected_regime,
        portfolio_heat=portfolio_heat,
        action=rl_action,
        trades_this_step=trades_this_step,
        signals=signal_snapshot
    )

    # ── 6. Learn from completed episode ──
    episode_ended = False
    if isinstance(step_result, dict) and "training_pairs" in step_result:
        episode_ended = True
        training_pairs = step_result.get("training_pairs", [])
        if training_pairs:
            q_learner.update_from_episode(training_pairs)
            terminal = step_result.get("terminal", {})
            logger.info(
                f"Episode complete! PnL: ${terminal.get('daily_pnl', 0):+.2f} "
                f"({terminal.get('daily_pnl_pct', 0):+.2%}) | "
                f"Trained on {len(training_pairs)} steps"
            )
            if send_telegram:
                pnl = terminal.get("daily_pnl", 0)
                pnl_pct = terminal.get("daily_pnl_pct", 0)
                emoji = "\U0001f4c8" if pnl > 0 else "\U0001f4c9" if pnl < 0 else "\u27a1\ufe0f"
                ep_stats = episode_mgr.get_episode_stats()
                msg = (
                    f"{emoji} <b>Episode Complete</b>\n\n"
                    f"Daily PnL: ${pnl:+.2f} ({pnl_pct:+.2%})\n"
                    f"Max Drawdown: {terminal.get('max_drawdown', 0):.2%}\n"
                    f"Trades: {terminal.get('total_trades', 0)}\n"
                    f"Sharpe: {terminal.get('sharpe_proxy', 0):.2f}\n\n"
                    f"Episodes: {ep_stats.get('episodes', 0)} | "
                    f"Win Rate: {ep_stats.get('win_rate', 0):.0%}\n"
                    f"Q-States: {len(q_learner.q_table)} | "
                    f"\u03b5: {q_learner.epsilon:.3f}"
                )
                send_telegram(msg)

    # TD(0) inline update from consecutive steps
    elif step_result and isinstance(step_result, dict) and "state" in step_result:
        ep = episode_mgr.current_episode
        if ep and len(ep.steps) >= 2:
            prev = ep.steps[-2]
            q_learner.td_update(
                tuple(prev["state"]), prev["action"],
                prev["reward"], current_state
            )

    # ── 7. Score positions ──
    position_scores = []
    for pos in positions:
        sym = pos.get("symbol", "")
        ot = tracker.trades.get("open", {}).get(sym, {})
        sigs = ot.get("signals", {})
        if sigs:
            sc = engine.score_opportunity(
                signals=sigs,
                sector=ot.get("sector", "other"),
                time_bucket=ot.get("time_bucket")
            )
            position_scores.append({
                "symbol": sym,
                "score": sc["score"],
                "recommendation": sc["recommendation"],
                "unrealized_pnl": float(pos.get("unrealized_plpc", 0)) * 100
            })

    # ── 8. Periodic report ──
    now = time.time()
    if now - _last_report > REPORT_INTERVAL or episode_ended:
        _last_report = now
        summary = engine.get_learning_summary()
        logger.info(f"\n{engine.report()}\n\n{q_learner.report()}")
        if send_telegram and (summary["total_updates"] > 0 or episode_ended):
            lines = ["\U0001f9e0 <b>Adaptive + RL Report</b>\n"]
            lines.append(
                f"Bayesian: {summary['total_updates']} updates | "
                f"Win: {summary['global_win_rate']:.0%} | "
                f"Regime: {summary['regime']}"
            )
            lines.append(
                f"Q-States: {len(q_learner.q_table)} | "
                f"Episodes: {q_learner.stats.get('episodes_learned', 0)} | "
                f"\u03b5: {q_learner.epsilon:.3f}"
            )
            lines.append(f"\nRL recommends: <b>{rl_action}</b>")
            send_telegram("\n".join(lines))

    return {
        "newly_closed": trades_this_step,
        "regime": detected_regime,
        "rl_action": rl_action,
        "rl_q_value": q_rec["q_value"],
        "rl_confidence": q_rec["confidence"],
        "rl_exploration": was_exploration,
        "episode_active": episode_mgr.current_episode is not None,
        "episode_step": (
            len(episode_mgr.current_episode.steps)
            if episode_mgr.current_episode else 0
        ),
        "episode_ended": episode_ended,
        "q_states_explored": len(q_learner.q_table),
        "q_episodes_learned": q_learner.stats.get("episodes_learned", 0),
        "position_scores": position_scores,
        "total_learning_updates": engine.db["global_stats"].get("total_updates", 0),
        "engine_status": (
            "learning" if engine.db["global_stats"].get("total_updates", 0)
            >= engine.min_trades else "collecting"
        )
    }


def backfill_from_alpaca(days=30, send_telegram=None):
    """Bootstrap by backfilling from Alpaca order history."""
    import requests as req

    config = load_config()
    tracker = TradeTracker(config=config,
                           trade_log_path=os.path.join(BASE_DIR, "trade_log.json"))
    engine = AdaptiveEngine(config=config,
                            db_path=os.path.join(BASE_DIR, "learning_db.json"),
                            history_path=os.path.join(BASE_DIR, "weight_history.json"))

    base = os.getenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    headers = {
        "APCA-API-KEY-ID": os.getenv("APCA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.getenv("APCA_API_SECRET_KEY", ""),
    }

    from datetime import timedelta
    after = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    url = f"{base}/v2/orders"
    params = {"status": "filled", "limit": 500, "direction": "asc", "after": after}

    try:
        r = req.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        all_orders = r.json()
    except Exception as e:
        logger.error(f"Failed to fetch orders: {e}")
        return {"error": str(e)}

    buys = {}
    trades_reconstructed = []

    for order in all_orders:
        symbol = order.get("symbol", "")
        side = order.get("side", "")
        price = float(order.get("filled_avg_price", 0))
        qty = float(order.get("filled_qty", 0))

        if side == "buy":
            buys.setdefault(symbol, []).append({
                "price": price, "qty": qty,
                "time": order.get("filled_at", "")
            })
        elif side == "sell" and symbol in buys and buys[symbol]:
            buy = buys[symbol].pop(0)
            pnl_ps = price - buy["price"]
            pnl_total = pnl_ps * min(qty, buy["qty"])
            pnl_pct = (pnl_ps / buy["price"]) * 100 if buy["price"] > 0 else 0
            trades_reconstructed.append({
                "symbol": symbol, "entry_price": buy["price"],
                "exit_price": price, "qty": min(qty, buy["qty"]),
                "pnl_total": round(pnl_total, 4), "pnl_pct": round(pnl_pct, 4),
                "win": pnl_total > 0, "side": "buy",
                "entry_time": buy["time"],
                "exit_time": order.get("filled_at", ""),
                "signals": {}, "signal_outcomes": {},
                "sector": "other", "time_bucket": "unknown",
                "exit_reason": "historical", "status": "closed",
                "metadata": {"backfilled": True}
            })

    tracker.trades["closed"].extend(trades_reconstructed)
    tracker._save_trades()

    gs = engine.db["global_stats"]
    for trade in trades_reconstructed:
        gs["total_updates"] = gs.get("total_updates", 0) + 1
        if trade["win"]:
            gs["total_wins"] = gs.get("total_wins", 0) + 1
        else:
            gs["total_losses"] = gs.get("total_losses", 0) + 1
    gs["last_update"] = datetime.utcnow().isoformat() + "Z"
    engine._save_db()

    wins = sum(1 for t in trades_reconstructed if t["win"])
    losses = len(trades_reconstructed) - wins
    total_t = wins + losses

    if total_t > 0:
        msg = (
            f"\U0001f4da Backfill: {total_t} trades from {days} days | "
            f"W/L: {wins}/{losses} ({wins/total_t:.0%})"
        )
    else:
        msg = f"\U0001f4da Backfill: No completed pairs in last {days} days"

    logger.info(msg)
    if send_telegram:
        send_telegram(msg)

    return {
        "trades_reconstructed": total_t,
        "wins": wins, "losses": losses,
        "orders_fetched": len(all_orders)
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        print(f"Backfilling from last {days} days...")
        result = backfill_from_alpaca(days=days)
        print(json.dumps(result, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "report":
        config = load_config()
        engine = AdaptiveEngine(config=config,
                                db_path=os.path.join(BASE_DIR, "learning_db.json"))
        q = QLearner(config=config)
        print(engine.report())
        print()
        print(q.report())
    else:
        print("Running adaptive cycle...")
        result = run_adaptive_cycle()
        print(json.dumps(result, indent=2, default=str))
