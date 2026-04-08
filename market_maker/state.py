from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class ExposureSnapshot:
    total_exposure_usd: float
    daily_realized_pnl_usd: float


class MMStateStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS mm_cycles (
                cycle_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                markets_quoted INTEGER NOT NULL,
                orders_posted INTEGER NOT NULL,
                orders_cancelled INTEGER NOT NULL,
                fills_received INTEGER NOT NULL,
                total_pnl REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS mm_orders (
                order_id TEXT PRIMARY KEY,
                cycle_id TEXT,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mm_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                fee_usd REAL NOT NULL DEFAULT 0,
                rebate_usd REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mm_inventory (
                token_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                position_shares REAL NOT NULL DEFAULT 0,
                avg_entry_price REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mm_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                bid_price REAL,
                bid_size REAL,
                ask_price REAL,
                ask_size REAL,
                fair_value REAL,
                spread REAL,
                skew_applied REAL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mm_orders_status ON mm_orders(status);
            CREATE INDEX IF NOT EXISTS idx_mm_orders_market ON mm_orders(market_id, status);
            CREATE INDEX IF NOT EXISTS idx_mm_fills_created ON mm_fills(created_at);
            CREATE INDEX IF NOT EXISTS idx_mm_fills_order ON mm_fills(order_id);
            CREATE INDEX IF NOT EXISTS idx_mm_inventory_market ON mm_inventory(market_id);
            CREATE INDEX IF NOT EXISTS idx_mm_quotes_cycle ON mm_quotes(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_mm_cycles_started ON mm_cycles(started_at);
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── cycles ──

    def record_cycle(self, cycle_id: str, markets_quoted: int, orders_posted: int,
                     orders_cancelled: int, fills_received: int, total_pnl: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO mm_cycles VALUES (?,?,?,?,?,?,?)",
            (cycle_id, now, markets_quoted, orders_posted, orders_cancelled, fills_received, total_pnl),
        )
        self.conn.commit()

    # ── orders ──

    def record_order(self, order_id: str, cycle_id: str, market_id: str, token_id: str,
                     side: str, price: float, size: float, status: str, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO mm_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, cycle_id, market_id, token_id, side, price, size, status, mode, now, now),
        )
        self.conn.commit()

    def update_order_status(self, order_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE mm_orders SET status=?, updated_at=? WHERE order_id=?", (status, now, order_id))
        self.conn.commit()

    def get_open_orders(self, market_id: str | None = None) -> list[sqlite3.Row]:
        if market_id:
            return self.conn.execute(
                "SELECT * FROM mm_orders WHERE status IN ('submitted','simulated') AND market_id=?",
                (market_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM mm_orders WHERE status IN ('submitted','simulated')",
        ).fetchall()

    def count_open_orders(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM mm_orders WHERE status IN ('submitted','simulated')"
        ).fetchone()
        return int(row["c"]) if row else 0

    def count_orders_last_minute(self) -> int:
        since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM mm_orders WHERE created_at >= ?", (since,)
        ).fetchone()
        return int(row["c"]) if row else 0

    # ── fills ──

    def record_fill(self, fill_id: str, order_id: str, market_id: str, token_id: str,
                    side: str, price: float, size: float, fee_usd: float, rebate_usd: float,
                    realized_pnl: float, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO mm_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (fill_id, order_id, market_id, token_id, side, price, size, fee_usd, rebate_usd, realized_pnl, mode, now),
        )
        self.conn.commit()

    def daily_realized_pnl(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl),0) p FROM mm_fills WHERE substr(created_at,1,10)=?", (today,)
        ).fetchone()
        return float(row["p"]) if row else 0.0

    def recent_fills(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM mm_fills ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()

    # ── inventory ──

    def get_inventory(self, token_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM mm_inventory WHERE token_id=?", (token_id,)
        ).fetchone()

    def get_all_inventory(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM mm_inventory").fetchall()

    def update_inventory(self, token_id: str, market_id: str, outcome: str,
                         position_delta: float, fill_price: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_inventory(token_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO mm_inventory VALUES (?,?,?,?,?,?)",
                (token_id, market_id, outcome, position_delta, fill_price, now),
            )
        else:
            old_pos = float(existing["position_shares"])
            old_avg = float(existing["avg_entry_price"])
            new_pos = old_pos + position_delta
            if abs(new_pos) < 1e-9:
                new_avg = 0.0
            elif position_delta > 0 and old_pos >= 0:
                total_cost = old_avg * old_pos + fill_price * position_delta
                new_avg = total_cost / new_pos if new_pos > 0 else 0.0
            else:
                new_avg = old_avg
            self.conn.execute(
                "UPDATE mm_inventory SET position_shares=?, avg_entry_price=?, updated_at=? WHERE token_id=?",
                (new_pos, new_avg, now, token_id),
            )
        self.conn.commit()

    def total_exposure_usd(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(ABS(position_shares) * avg_entry_price), 0) e FROM mm_inventory"
        ).fetchone()
        return float(row["e"]) if row else 0.0

    def exposure_snapshot(self) -> ExposureSnapshot:
        return ExposureSnapshot(
            total_exposure_usd=self.total_exposure_usd(),
            daily_realized_pnl_usd=self.daily_realized_pnl(),
        )

    # ── quotes ──

    def record_quote(self, cycle_id: str, market_id: str, token_id: str, outcome: str,
                     bid_price: float | None, bid_size: float | None,
                     ask_price: float | None, ask_size: float | None,
                     fair_value: float | None, spread: float | None,
                     skew_applied: float | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO mm_quotes (cycle_id,market_id,token_id,outcome,bid_price,bid_size,"
            "ask_price,ask_size,fair_value,spread,skew_applied,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, market_id, token_id, outcome, bid_price, bid_size,
             ask_price, ask_size, fair_value, spread, skew_applied, now),
        )
        self.conn.commit()

    # ── aggregate queries for UI ──

    def cycle_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) c FROM mm_cycles").fetchone()
        return int(row["c"]) if row else 0

    def total_fills(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) c FROM mm_fills").fetchone()
        return int(row["c"]) if row else 0

    def recent_cycles(self, limit: int = 60) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM mm_cycles ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def pnl_series(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT created_at, realized_pnl FROM mm_fills ORDER BY created_at ASC LIMIT ?", (limit,)
        ).fetchall()

    def latest_quotes(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM mm_quotes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
