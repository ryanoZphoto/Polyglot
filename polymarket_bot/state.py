from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .scanner import NearMissCandidate


@dataclass(frozen=True)
class ExposureSnapshot:
    open_exposure_usd: float
    daily_realized_pnl_usd: float


class StateStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cycles (
                cycle_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                scanned_markets INTEGER NOT NULL,
                opportunities INTEGER NOT NULL,
                executed INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                group_key TEXT NOT NULL,
                event_key TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                cost_usd REAL NOT NULL,
                expected_profit_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                market_slug TEXT NOT NULL,
                status TEXT NOT NULL,
                order_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                filled_usd REAL NOT NULL,
                realized_pnl_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS near_misses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                rank_index INTEGER NOT NULL,
                group_key TEXT NOT NULL,
                legs_considered INTEGER NOT NULL,
                sum_ask REAL NOT NULL,
                payout_per_share REAL NOT NULL,
                edge REAL NOT NULL,
                min_edge_required REAL NOT NULL,
                edge_gap REAL NOT NULL,
                estimated_profit_usd REAL NOT NULL,
                min_profit_required REAL NOT NULL,
                profit_gap_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cycle_diagnostics (
                cycle_id TEXT PRIMARY KEY,
                diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def has_trade(self, trade_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM trades WHERE trade_id = ? LIMIT 1",
            (trade_id,),
        ).fetchone()
        return row is not None

    def count_orders_last_minute(self) -> int:
        since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE created_at >= ?",
            (since,),
        ).fetchone()
        return int(row["c"]) if row else 0

    def event_open_exposure(self, event_key: str) -> float:
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS exposure
            FROM trades
            WHERE event_key = ? AND status IN ('submitted', 'partially_filled')
            """,
            (event_key,),
        ).fetchone()
        return float(row["exposure"]) if row else 0.0

    def exposure_snapshot(self) -> ExposureSnapshot:
        open_row = self.conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0) AS open_exposure
            FROM trades
            WHERE status IN ('submitted', 'partially_filled')
            """
        ).fetchone()
        today = datetime.now(timezone.utc).date().isoformat()
        pnl_row = self.conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl_usd), 0) AS pnl
            FROM fills
            WHERE substr(created_at, 1, 10) = ?
            """,
            (today,),
        ).fetchone()
        return ExposureSnapshot(
            open_exposure_usd=float(open_row["open_exposure"]) if open_row else 0.0,
            daily_realized_pnl_usd=float(pnl_row["pnl"]) if pnl_row else 0.0,
        )

    def record_trade(
        self,
        trade_id: str,
        cycle_id: str,
        group_key: str,
        event_key: str,
        mode: str,
        status: str,
        cost_usd: float,
        expected_profit_usd: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO trades
            (trade_id, cycle_id, group_key, event_key, mode, status, cost_usd, expected_profit_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                cycle_id,
                group_key,
                event_key,
                mode,
                status,
                cost_usd,
                expected_profit_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def update_trade_status(self, trade_id: str, status: str) -> None:
        self.conn.execute("UPDATE trades SET status = ? WHERE trade_id = ?", (status, trade_id))
        self.conn.commit()

    def record_order(
        self,
        trade_id: str,
        token_id: str,
        market_slug: str,
        status: str,
        order_id: str | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO orders (trade_id, token_id, market_slug, status, order_id, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                token_id,
                market_slug,
                status,
                order_id,
                error,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def record_fill(self, trade_id: str, filled_usd: float, realized_pnl_usd: float) -> None:
        self.conn.execute(
            """
            INSERT INTO fills (trade_id, filled_usd, realized_pnl_usd, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (trade_id, filled_usd, realized_pnl_usd, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def record_cycle(self, cycle_id: str, scanned_markets: int, opportunities: int, executed: int) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO cycles (cycle_id, started_at, scanned_markets, opportunities, executed)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                datetime.now(timezone.utc).isoformat(),
                scanned_markets,
                opportunities,
                executed,
            ),
        )
        self.conn.commit()

    def record_near_misses(self, cycle_id: str, near_misses: list[NearMissCandidate]) -> None:
        for idx, nm in enumerate(near_misses):
            self.conn.execute(
                """
                INSERT INTO near_misses
                (
                    cycle_id, rank_index, group_key, legs_considered, sum_ask, payout_per_share,
                    edge, min_edge_required, edge_gap, estimated_profit_usd, min_profit_required,
                    profit_gap_usd, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    idx + 1,
                    nm.group_key,
                    nm.legs_considered,
                    nm.sum_ask,
                    nm.payout_per_share,
                    nm.edge,
                    nm.min_edge_required,
                    nm.edge_gap,
                    nm.estimated_profit_usd,
                    nm.min_profit_required,
                    nm.profit_gap_usd,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        self.conn.commit()

    def record_cycle_diagnostics(self, cycle_id: str, diagnostics: dict[str, int]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO cycle_diagnostics (cycle_id, diagnostics_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                cycle_id,
                json.dumps(diagnostics, ensure_ascii=True),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def latest_cycle_summary(self) -> dict[str, float | int | str] | None:
        row = self.conn.execute(
            """
            SELECT cycle_id, started_at, scanned_markets, opportunities, executed
            FROM cycles
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "cycle_id": str(row["cycle_id"]),
            "started_at": str(row["started_at"]),
            "scanned_markets": int(row["scanned_markets"]),
            "opportunities": int(row["opportunities"]),
            "executed": int(row["executed"]),
        }
