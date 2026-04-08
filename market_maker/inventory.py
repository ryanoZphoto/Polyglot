from __future__ import annotations

from .config import MMConfig
from .state import MMStateStore
from .types import InventoryState


class InventoryManager:
    def __init__(self, config: MMConfig, state: MMStateStore):
        self.config = config
        self.state = state

    def get_position(self, token_id: str, market_id: str, outcome: str) -> InventoryState:
        row = self.state.get_inventory(token_id)
        if row is None:
            return InventoryState(
                market_id=market_id,
                token_id=token_id,
                outcome=outcome,
                position_shares=0.0,
                avg_entry_price=0.0,
                unrealized_pnl=0.0,
                side="flat",
            )
        pos = float(row["position_shares"])
        avg = float(row["avg_entry_price"])
        if pos > 0.01:
            side = "long"
        elif pos < -0.01:
            side = "short"
        else:
            side = "flat"
        return InventoryState(
            market_id=market_id,
            token_id=token_id,
            outcome=outcome,
            position_shares=pos,
            avg_entry_price=avg,
            unrealized_pnl=0.0,  # updated externally with current price
            side=side,
        )

    def compute_skew(self, position_shares: float) -> float:
        """
        Returns a price adjustment.

        Positive position (long) -> negative skew (lower bid to discourage buying more,
        lower ask to encourage selling).
        Negative position (short) -> positive skew (raise ask, raise bid).

        Magnitude is proportional to position / max_inventory * skew_factor * half_spread.
        """
        if abs(position_shares) < 0.01:
            return 0.0
        utilization = position_shares / max(1.0, self.config.max_inventory_per_market)
        utilization = max(-1.0, min(1.0, utilization))
        return -utilization * self.config.skew_factor * self.config.half_spread

    def record_fill(self, token_id: str, market_id: str, outcome: str,
                    side: str, fill_price: float, fill_size: float) -> None:
        delta = fill_size if side == "BUY" else -fill_size
        self.state.update_inventory(token_id, market_id, outcome, delta, fill_price)

    def is_at_limit(self, token_id: str) -> bool:
        row = self.state.get_inventory(token_id)
        if row is None:
            return False
        return abs(float(row["position_shares"])) >= self.config.max_inventory_per_market
