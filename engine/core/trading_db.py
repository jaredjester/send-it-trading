"""
SQLite database for atomic state persistence.

Replaces JSONL files with safe, concurrent database operations.
"""
import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import asdict

logger = logging.getLogger(__name__)

class TradingDatabase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    pnl REAL,
                    strategy TEXT,
                    signal_type TEXT,
                    score REAL,
                    outcome TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    qty REAL NOT NULL,
                    market_value REAL,
                    unrealized_pl REAL,
                    entry_price REAL,
                    stop_price REAL,
                    target_price REAL,
                    timestamp TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS configs (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            conn.commit()

    def record_trade(self, trade: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO trades
                (id, symbol, side, qty, price, timestamp, pnl, strategy, signal_type, score, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade['trade_id'],
                trade['symbol'],
                trade['side'],
                trade['qty'],
                trade['price'],
                trade['timestamp'],
                trade.get('pnl'),
                trade.get('strategy'),
                trade.get('signal_type'),
                trade.get('score'),
                trade.get('outcome')
            ))
            conn.commit()

    def get_trades(self, symbol: Optional[str] = None, limit: int = 1000) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM trades"
            params = []
            if symbol:
                query += " WHERE symbol = ?"
                params.append(symbol)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(zip(['id', 'symbol', 'side', 'qty', 'price', 'timestamp', 'pnl', 'strategy', 'signal_type', 'score', 'outcome'], row)) for row in rows]

    def update_trade_pnl(self, trade_id: str, pnl: float, outcome: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                UPDATE trades SET pnl = ?, outcome = ? WHERE id = ?
            ''', (pnl, outcome, trade_id))
            conn.commit()

    def record_position(self, position: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO positions
                (id, symbol, qty, market_value, unrealized_pl, entry_price, stop_price, target_price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                position['id'],
                position['symbol'],
                position['qty'],
                position.get('market_value'),
                position.get('unrealized_pl'),
                position.get('entry_price'),
                position.get('stop_price'),
                position.get('target_price'),
                position['timestamp']
            ))
            conn.commit()

    def get_positions(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM positions").fetchall()
            return [dict(zip(['id', 'symbol', 'qty', 'market_value', 'unrealized_pl', 'entry_price', 'stop_price', 'target_price', 'timestamp'], row)) for row in rows]

    def update_position(self, position_id: str, updates: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
            values = list(updates.values()) + [position_id]
            conn.execute(f'UPDATE positions SET {set_clause} WHERE id = ?', values)
            conn.commit()

    def remove_position(self, position_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
            conn.commit()

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM positions WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
            if row:
                return dict(zip(['id', 'symbol', 'qty', 'market_value', 'unrealized_pl', 'entry_price', 'stop_price', 'target_price', 'timestamp'], row))
            return None

    def log_event(self, event: str, details: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO journal (event, details, timestamp)
                VALUES (?, ?, datetime('now'))
            ''', (event, json.dumps(details)))
            conn.commit()

    def get_journal(self, limit: int = 100) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM journal ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            return [dict(zip(['id', 'event', 'details', 'timestamp'], row)) for row in rows]

# Global instance
DB_PATH = Path(__file__).parent.parent / "state" / "trading.db"
db = TradingDatabase(DB_PATH)