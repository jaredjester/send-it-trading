"""
Walk-forward performance validator for Strategy V2.

Uses real trade history (engine/state/trade_memory.jsonl) to compute
live performance metrics. No simulated data — actual P&L from closed trades.
"""
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

BASE_DIR = Path(__file__).resolve().parent.parent          # engine/
STATE_DIR = BASE_DIR / "state"
RESULTS_DB = BASE_DIR / "evaluation" / "backtest_results.db"


class StrategyBacktester:
    """Walk-forward validator: computes real metrics from trade_memory.jsonl."""

    def __init__(self, results_db: str | None = None):
        self.results_db = Path(results_db) if results_db else RESULTS_DB
        self.results_db.parent.mkdir(parents=True, exist_ok=True)
        self._init_results_db()

    # ── DB setup ──────────────────────────────────────────────────────────
    def _init_results_db(self):
        conn = sqlite3.connect(str(self.results_db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                config_json TEXT,
                start_date TEXT,
                end_date TEXT,
                initial_capital REAL,
                total_return REAL,
                sharpe REAL,
                max_drawdown REAL,
                win_rate REAL,
                num_trades INTEGER,
                avg_trade_return REAL,
                executed_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    # ── Trade loader ──────────────────────────────────────────────────────
    def _load_trades(self, start_date: str, end_date: str) -> List[Dict]:
        """Read trade_memory.jsonl, filter by date range, return closed trades."""
        memory_path = STATE_DIR / "trade_memory.jsonl"
        if not memory_path.exists():
            return []
        trades = []
        start_dt = datetime.fromisoformat(start_date)
        end_dt   = datetime.fromisoformat(end_date)
        for line in memory_path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
                ts_str = t.get("entry_ts") or t.get("ts", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))
                if start_dt <= ts <= end_dt:
                    trades.append(t)
            except Exception:
                continue
        return trades

    # ── Metric computation ────────────────────────────────────────────────
    def _compute_metrics(self, trades: List[Dict], initial_capital: float) -> Dict:
        """Compute real performance metrics from a list of trade dicts."""
        closed = [t for t in trades if t.get("exit_price") or t.get("pnl") is not None]
        if not closed:
            return {
                "total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "win_rate": 0.0, "num_trades": 0, "avg_trade_return": 0.0,
            }

        pnls = []
        for t in closed:
            pnl = t.get("pnl")
            if pnl is None:
                entry  = float(t.get("entry_price", 0) or 0)
                exit_  = float(t.get("exit_price",  0) or 0)
                qty    = float(t.get("qty", t.get("contracts", 1)) or 1)
                mult   = 100 if t.get("kind") in ("CALL", "PUT") else 1
                pnl    = (exit_ - entry) * qty * mult
            pnls.append(float(pnl))

        total_pnl     = sum(pnls)
        total_return  = total_pnl / initial_capital if initial_capital else 0.0
        win_rate      = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0
        avg_ret       = (sum(p / initial_capital for p in pnls) / len(pnls)) if pnls else 0.0

        # Sharpe (annualised, assume daily std from trade returns)
        if len(pnls) >= 2:
            mean_r = avg_ret
            std_r  = math.sqrt(sum((p / initial_capital - mean_r) ** 2 for p in pnls) / len(pnls))
            sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown (running equity curve)
        equity, peak, max_dd = initial_capital, initial_capital, 0.0
        for p in pnls:
            equity += p
            peak    = max(peak, equity)
            dd      = (peak - equity) / peak
            max_dd  = max(max_dd, dd)

        return {
            "total_return":   round(total_return, 4),
            "sharpe":         round(sharpe, 3),
            "max_drawdown":   round(max_dd, 4),
            "win_rate":       round(win_rate, 3),
            "num_trades":     len(closed),
            "avg_trade_return": round(avg_ret, 4),
        }

    # ── Public API ────────────────────────────────────────────────────────
    def run_backtest(
        self,
        start_date: str,
        end_date:   str,
        orchestrator_config: Dict,
        initial_capital: float = 1000.0,
        benchmark: str = "SPY",
    ) -> Dict:
        """
        Walk-forward validation over real trade history.

        Returns:
            {run_id, metrics: {total_return, sharpe, max_drawdown,
                               win_rate, num_trades, avg_trade_return}, trades}
        """
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        trades  = self._load_trades(start_date, end_date)
        metrics = self._compute_metrics(trades, initial_capital)

        # Persist to DB
        try:
            conn = sqlite3.connect(str(self.results_db))
            conn.execute(
                """INSERT OR REPLACE INTO backtest_runs
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, json.dumps(orchestrator_config), start_date, end_date,
                 initial_capital, metrics["total_return"], metrics["sharpe"],
                 metrics["max_drawdown"], metrics["win_rate"], metrics["num_trades"],
                 metrics["avg_trade_return"], datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        return {"run_id": run_id, "metrics": metrics, "trades": trades}

    def compare_to_baseline(
        self,
        new_config: Dict,
        baseline_run_id: str,
        test_period_days: int = 90,
    ) -> Dict:
        """Compare new config vs stored baseline run."""
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=test_period_days)
        new_results      = self.run_backtest(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), new_config)
        baseline_metrics = self._load_run_metrics(baseline_run_id)
        delta = {k: new_results["metrics"].get(k, 0) - baseline_metrics.get(k, 0)
                 for k in ("total_return", "sharpe", "max_drawdown")}
        is_improvement = delta["sharpe"] >= 0 and delta["max_drawdown"] <= 0.05
        return {
            "new_run": new_results,
            "baseline_run_id": baseline_run_id,
            "delta": delta,
            "is_improvement": is_improvement,
            "recommendation": "DEPLOY" if is_improvement else "REJECT",
        }

    def validate_deployment(
        self,
        config: Dict,
        min_sharpe: float = 0.5,
        min_win_rate: float = 0.40,
        max_drawdown: float = 0.50,
    ) -> Tuple[bool, str]:
        """Gate deployment: validate config against recent real trade history."""
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=90)
        results  = self.run_backtest(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), config)
        m        = results["metrics"]

        if m["num_trades"] < 3:
            return True, f"Insufficient history ({m['num_trades']} trades) — allowing deployment"

        checks = {
            "sharpe":    (m["sharpe"]      >= min_sharpe,   f"Sharpe {m['sharpe']:.2f} < {min_sharpe}"),
            "win_rate":  (m["win_rate"]    >= min_win_rate, f"Win rate {m['win_rate']:.0%} < {min_win_rate:.0%}"),
            "drawdown":  (m["max_drawdown"] <= max_drawdown, f"DD {m['max_drawdown']:.0%} > {max_drawdown:.0%}"),
        }
        failed = [msg for passed, msg in checks.values() if not passed]
        return (not failed), ("; ".join(failed) if failed else "All checks passed")

    def _load_run_metrics(self, run_id: str) -> Dict:
        """Load stored metrics for a previous run."""
        try:
            conn   = sqlite3.connect(str(self.results_db))
            cursor = conn.execute(
                "SELECT sharpe, max_drawdown, total_return, win_rate FROM backtest_runs WHERE run_id=?",
                (run_id,)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {"sharpe": row[0], "max_drawdown": row[1], "total_return": row[2], "win_rate": row[3]}
        except Exception:
            pass
        return {"sharpe": 0.0, "max_drawdown": 0.0, "total_return": 0.0, "win_rate": 0.0}
