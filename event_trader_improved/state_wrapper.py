"""State wrapper for improved event trader."""

import logging
from datetime import datetime, timezone
from event_trader.state import EVStateStore

logger = logging.getLogger(__name__)


class ImprovedStateWrapper:
    """Wrapper around EVStateStore with improved interface."""
    
    def __init__(self, db_path: str):
        self.store = EVStateStore(db_path)
    
    def is_paused(self) -> bool:
        """Check if bot is paused."""
        try:
            controls = self.store.read_controls()
            return controls.get("paused", False)
        except Exception:
            return False
    
    def get_open_positions(self) -> list[dict]:
        """Fetch all currently open positions."""
        try:
            return self.store.get_open_positions()
        except Exception:
            return []

    def update_position_price(self, pos_id: str, price: float):
        """Update current price and high-water mark for a position."""
        try:
            # We use the store's connection directly since EVStateStore 
            # doesn't have a simple high-water update method
            self.store.conn.execute(
                "UPDATE ev_positions SET current_price = ?, high_water_price = MAX(high_water_price, ?) WHERE position_id = ?",
                (price, price, pos_id)
            )
            self.store.conn.commit()
        except Exception as e:
            logger.warning("Failed to update position price: %s", e)

    def close_position(self, pos_id: str, price: float, reason: str):
        """Mark a position as closed and record realized PnL."""
        try:
            # 1. Fetch position details for PnL calculation
            pos = self.store.conn.execute(
                "SELECT entry_price, contracts, cost_basis_usd FROM ev_positions WHERE position_id = ?",
                (pos_id,)
            ).fetchone()
            
            if not pos: return
            
            realized_pnl = (price * pos["contracts"]) - pos["cost_basis_usd"]
            now = datetime.now(timezone.utc).isoformat()
            
            # 2. Update Position
            self.store.conn.execute(
                "UPDATE ev_positions SET status = 'closed', current_price = ?, closed_at = ?, close_reason = ?, realized_pnl = ? WHERE position_id = ?",
                (price, now, reason, realized_pnl, pos_id)
            )
            
            # 3. Record a Fill (for UI)
            import uuid
            fill_id = f"fill_{uuid.uuid4().hex[:8]}"
            self.store.conn.execute(
                "INSERT INTO ev_fills (fill_id, order_id, market_id, token_id, side, price, size, realized_pnl, mode, created_at) SELECT ?, 'DRY_ORDER', market_id, token_id, 'SELL', ?, ?, ?, 'dry_run', ? FROM ev_positions WHERE position_id = ?",
                (fill_id, price, pos["contracts"], realized_pnl, now, pos_id)
            )
            
            self.store.conn.commit()
        except Exception as e:
            logger.warning("Failed to close position: %s", e)

    def record_cycle(self, cycle_id: str, cycle_number: int, scanned_markets: int, 
                    signals_found: int, entries_placed: int, exits_placed: int = 0) -> None:
        """Record cycle completion."""
        try:
            # Calculate current total unrealized and realized PnL for the cycle report
            stats = self.store.conn.execute(
                "SELECT SUM((current_price - entry_price) * contracts) as unrealized, (SELECT SUM(realized_pnl) FROM ev_positions WHERE status='closed') as realized FROM ev_positions WHERE status='open'"
            ).fetchone()
            
            unrealized = stats["unrealized"] or 0.0
            realized = stats["realized"] or 0.0
            
            self.store.record_cycle(
                cycle_id=cycle_id,
                cycle_number=cycle_number,
                markets_scanned=scanned_markets,
                signals_found=signals_found,
                entries_placed=entries_placed,
                exits_placed=exits_placed,
                fills_detected=entries_placed + exits_placed,
                positions_open=len(self.get_open_positions()),
                unrealized_pnl=unrealized,
                realized_pnl=realized,
            )
        except Exception as e:
            logger.warning("Failed to record cycle: %s", e)

    def record_dry_run_signal(self, cycle_id: str, sig) -> None:
        """Record a signal for UI visibility."""
        try:
            import uuid
            sig_id = f"sig_{uuid.uuid4().hex[:8]}"
            self.store.record_signal(
                signal_id=sig_id,
                cycle_id=cycle_id,
                market_id=sig.market_id,
                token_id=sig.token_id,
                outcome=sig.outcome,
                question=sig.question,
                current_price=sig.price,
                entry_price=sig.price,
                target_price=sig.price * 1.2,
                stop_loss_price=sig.price * 0.8,
                confidence=sig.edge,
                reason=f"[{sig.strategy}] Edge detected: {sig.edge:.1%}",
                book_bid=sig.price - 0.005,
                book_ask=sig.price + 0.005,
                book_spread=0.01,
                bid_depth=100.0,
                ask_depth=100.0,
                acted_on=True
            )
        except Exception as e:
            logger.warning("Failed to record signal: %s", e)

    def record_dry_run_position(self, sig) -> None:
        """Record a dry-run position for UI visibility."""
        try:
            import uuid
            pos_id = f"pos_{uuid.uuid4().hex[:8]}"
            contracts = sig.size_usd / sig.price
            self.store.conn.execute(
                "INSERT INTO ev_positions (position_id, market_id, token_id, outcome, question, entry_price, current_price, high_water_price, contracts, cost_basis_usd, target_price, stop_loss_price, status, mode, entry_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pos_id, sig.market_id, sig.token_id, sig.outcome, sig.question, 
                 sig.price, sig.price, sig.price, contracts, sig.size_usd, 
                 sig.target_price, sig.stop_loss_price, "open", "dry_run", 
                 datetime.now(timezone.utc).isoformat())
            )
            self.store.conn.commit()
        except Exception as e:
            logger.warning("Failed to record position: %s", e)
    
    def close(self) -> None:
        """Close state store."""
        try:
            self.store.close()
        except Exception:
            pass
