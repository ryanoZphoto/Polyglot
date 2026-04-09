# Copy from event_trader/state.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class EVPosition:
    token_id: str
    market_slug: str
    outcome: str
    entry_price: float
    shares: float
    entry_time: str
    stop_loss: float
    take_profit: float
    trailing_stop: float | None
    highest_price: float
    cost_usd: float
    current_value_usd: float
    unrealized_pnl_usd: float


class EVStateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                token_id TEXT PRIMARY KEY,
                market_slug TEXT,
                outcome TEXT,
                entry_price REAL,
                shares REAL,
                entry_time TEXT,
                stop_loss REAL,
                take_profit REAL,
                trailing_stop REAL,
                highest_price REAL,
                cost_usd REAL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT,
                timestamp TEXT,
                token_id TEXT,
                market_slug TEXT,
                outcome TEXT,
                action TEXT,
                price REAL,
                shares REAL,
                pnl_usd REAL,
                reason TEXT
            )
        """)
        self.conn.commit()

    def get_positions(self) -> list[EVPosition]:
        rows = self.conn.execute("SELECT * FROM positions").fetchall()
        positions = []
        for row in rows:
            positions.append(EVPosition(
                token_id=row[0],
                market_slug=row[1],
                outcome=row[2],
                entry_price=row[3],
                shares=row[4],
                entry_time=row[5],
                stop_loss=row[6],
                take_profit=row[7],
                trailing_stop=row[8],
                highest_price=row[9],
                cost_usd=row[10],
                current_value_usd=0.0,
                unrealized_pnl_usd=0.0,
            ))
        return positions

    def save_position(self, pos: EVPosition):
        self.conn.execute("""
            INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pos.token_id, pos.market_slug, pos.outcome, pos.entry_price, pos.shares,
              pos.entry_time, pos.stop_loss, pos.take_profit, pos.trailing_stop,
              pos.highest_price, pos.cost_usd))
        self.conn.commit()

    def delete_position(self, token_id: str):
        self.conn.execute("DELETE FROM positions WHERE token_id = ?", (token_id,))
        self.conn.commit()

    def record_trade(self, cycle_id: str, token_id: str, market_slug: str, outcome: str,
                     action: str, price: float, shares: float, pnl_usd: float, reason: str):
        self.conn.execute("""
            INSERT INTO trades (cycle_id, timestamp, token_id, market_slug, outcome, action, price, shares, pnl_usd, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cycle_id, datetime.now(timezone.utc).isoformat(), token_id, market_slug, outcome, action, price, shares, pnl_usd, reason))
        self.conn.commit()

    def close(self):
        self.conn.close()