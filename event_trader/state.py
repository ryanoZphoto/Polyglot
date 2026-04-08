from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class EVStateStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ev_cycles (
                cycle_id TEXT PRIMARY KEY,
                cycle_number INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                markets_scanned INTEGER NOT NULL DEFAULT 0,
                signals_found INTEGER NOT NULL DEFAULT 0,
                entries_placed INTEGER NOT NULL DEFAULT 0,
                exits_placed INTEGER NOT NULL DEFAULT 0,
                fills_detected INTEGER NOT NULL DEFAULT 0,
                positions_open INTEGER NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ev_signals (
                signal_id TEXT PRIMARY KEY,
                cycle_id TEXT,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                question TEXT NOT NULL,
                current_price REAL NOT NULL,
                entry_price REAL NOT NULL,
                target_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                book_bid REAL,
                book_ask REAL,
                book_spread REAL,
                bid_depth REAL,
                ask_depth REAL,
                acted_on INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ev_positions (
                position_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                question TEXT NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                high_water_price REAL NOT NULL,
                contracts REAL NOT NULL,
                cost_basis_usd REAL NOT NULL,
                target_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                mode TEXT NOT NULL DEFAULT 'dry_run',
                realized_pnl REAL NOT NULL DEFAULT 0,
                entry_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS ev_orders (
                order_id TEXT PRIMARY KEY,
                position_id TEXT,
                cycle_id TEXT,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ev_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                position_id TEXT,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                notional_usd REAL NOT NULL DEFAULT 0,
                fee_usd REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ev_positions_status ON ev_positions(status);
            CREATE INDEX IF NOT EXISTS idx_ev_positions_token ON ev_positions(token_id, status);
            CREATE INDEX IF NOT EXISTS idx_ev_orders_status ON ev_orders(status);
            CREATE INDEX IF NOT EXISTS idx_ev_orders_position ON ev_orders(position_id);
            CREATE INDEX IF NOT EXISTS idx_ev_fills_created ON ev_fills(created_at);
            CREATE INDEX IF NOT EXISTS idx_ev_signals_cycle ON ev_signals(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_ev_cycles_started ON ev_cycles(started_at);
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── cycles ──

    def record_cycle(self, cycle_id: str, cycle_number: int, markets_scanned: int,
                     signals_found: int, entries_placed: int, exits_placed: int,
                     fills_detected: int, positions_open: int,
                     unrealized_pnl: float, realized_pnl: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO ev_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, cycle_number, now, markets_scanned, signals_found,
             entries_placed, exits_placed, fills_detected, positions_open,
             unrealized_pnl, realized_pnl),
        )
        self.conn.commit()

    # ── signals ──

    def record_signal(self, signal_id: str, cycle_id: str, market_id: str,
                      token_id: str, outcome: str, question: str,
                      current_price: float, entry_price: float,
                      target_price: float, stop_loss_price: float,
                      confidence: float, reason: str,
                      book_bid: float, book_ask: float, book_spread: float,
                      bid_depth: float, ask_depth: float,
                      acted_on: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO ev_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (signal_id, cycle_id, market_id, token_id, outcome, question,
             current_price, entry_price, target_price, stop_loss_price,
             confidence, reason, book_bid, book_ask, book_spread,
             bid_depth, ask_depth, 1 if acted_on else 0, now),
        )
        self.conn.commit()

    def was_signal_recently_seen(self, token_id: str, hours: int = 0, minutes: int = 0) -> bool:
        """Avoid re-signaling the same token too soon."""
        total_minutes = hours * 60 + minutes
        if total_minutes <= 0:
            total_minutes = 240
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM ev_signals WHERE token_id=? AND created_at > datetime('now', ?)",
            (token_id, f"-{total_minutes} minutes"),
        ).fetchone()
        return int(row["c"]) > 0 if row else False

    # ── positions ──

    def open_position(self, position_id: str, market_id: str, token_id: str,
                      outcome: str, question: str, entry_price: float,
                      contracts: float, cost_basis_usd: float,
                      target_price: float, stop_loss_price: float, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO ev_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (position_id, market_id, token_id, outcome, question,
             entry_price, entry_price, entry_price,
             contracts, cost_basis_usd, target_price, stop_loss_price,
             "open", mode, 0.0, now, None, None),
        )
        self.conn.commit()

    def update_position_price(self, position_id: str, current_price: float) -> None:
        row = self.conn.execute(
            "SELECT high_water_price FROM ev_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
        hwp = current_price
        if row and float(row["high_water_price"]) > current_price:
            hwp = float(row["high_water_price"])
        self.conn.execute(
            "UPDATE ev_positions SET current_price=?, high_water_price=? WHERE position_id=?",
            (current_price, hwp, position_id),
        )
        self.conn.commit()

    def close_position(self, position_id: str, realized_pnl: float, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE ev_positions SET status='closed', realized_pnl=?, closed_at=?, close_reason=? "
            "WHERE position_id=?",
            (realized_pnl, now, reason, position_id),
        )
        self.conn.commit()

    def get_open_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_positions WHERE status='open' ORDER BY entry_at ASC",
        ).fetchall()

    def get_all_positions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_positions ORDER BY entry_at DESC",
        ).fetchall()

    def count_open_positions(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM ev_positions WHERE status='open'",
        ).fetchone()
        return int(row["c"]) if row else 0

    def has_position_for_token(self, token_id: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM ev_positions WHERE token_id=? AND status='open'",
            (token_id,),
        ).fetchone()
        return (int(row["c"]) > 0) if row else False

    def has_position_for_market(self, market_id: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM ev_positions WHERE market_id=? AND status='open'",
            (market_id,),
        ).fetchone()
        return (int(row["c"]) > 0) if row else False

    # ── orders ──

    def record_order(self, order_id: str, position_id: str, cycle_id: str,
                     market_id: str, token_id: str, side: str,
                     price: float, size: float, status: str,
                     mode: str, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO ev_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, position_id, cycle_id, market_id, token_id,
             side, price, size, status, mode, reason, now, now),
        )
        self.conn.commit()

    def update_order_status(self, order_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE ev_orders SET status=?, updated_at=? WHERE order_id=?",
            (status, now, order_id),
        )
        self.conn.commit()

    # ── fills ──

    def record_fill(self, fill_id: str, order_id: str, position_id: str,
                    market_id: str, token_id: str, side: str,
                    price: float, size: float, notional_usd: float,
                    fee_usd: float, realized_pnl: float, mode: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO ev_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fill_id, order_id, position_id, market_id, token_id,
             side, price, size, notional_usd, fee_usd, realized_pnl, mode, now),
        )
        self.conn.commit()

    # ── aggregate queries ──

    def total_cost_basis(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_basis_usd), 0) c "
            "FROM ev_positions WHERE status='open'",
        ).fetchone()
        return float(row["c"]) if row else 0.0

    def total_exposure_usd(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(contracts * current_price), 0) e "
            "FROM ev_positions WHERE status='open'",
        ).fetchone()
        return float(row["e"]) if row else 0.0

    def daily_realized_pnl(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) p FROM ev_fills "
            "WHERE substr(created_at,1,10)=?",
            (today,),
        ).fetchone()
        return float(row["p"]) if row else 0.0

    def total_unrealized_pnl(self) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM((current_price - entry_price) * contracts), 0) p "
            "FROM ev_positions WHERE status='open'",
        ).fetchone()
        return float(row["p"]) if row else 0.0

    def cycle_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) c FROM ev_cycles").fetchone()
        return int(row["c"]) if row else 0

    def total_fills(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) c FROM ev_fills").fetchone()
        return int(row["c"]) if row else 0

    def recent_cycles(self, limit: int = 60) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_cycles ORDER BY started_at DESC LIMIT ?", (limit,),
        ).fetchall()

    def recent_signals(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_signals ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()

    def recent_fills(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_fills ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()

    def closed_positions(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM ev_positions WHERE status='closed' ORDER BY closed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def exposure_by_market(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT market_id, question, SUM(contracts * current_price) exposure "
            "FROM ev_positions WHERE status='open' GROUP BY market_id",
        ).fetchall()
