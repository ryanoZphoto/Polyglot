from __future__ import annotations

from .config import MMConfig
from .state import MMStateStore
from .types import RiskDecision


class MMRiskEngine:
    def __init__(self, config: MMConfig, state: MMStateStore):
        self.config = config
        self.state = state

    def pre_cycle_check(self) -> RiskDecision:
        """Global checks before any quoting happens this cycle."""
        if self.config.emergency_stop:
            return RiskDecision(False, "emergency stop active")

        daily_pnl = self.state.daily_realized_pnl()
        if daily_pnl < -self.config.max_daily_loss_usd:
            return RiskDecision(False, f"daily loss limit hit: ${daily_pnl:.2f}")

        exposure = self.state.total_exposure_usd()
        if exposure >= self.config.max_total_exposure_usd:
            return RiskDecision(False, f"total exposure at limit: ${exposure:.2f}")

        return RiskDecision(True)

    def pre_order_check(self, market_id: str, token_id: str, size_usd: float) -> RiskDecision:
        """Per-order checks before posting a specific quote."""
        if self.config.emergency_stop:
            return RiskDecision(False, "emergency stop")

        inv = self.state.get_inventory(token_id)
        if inv is not None:
            pos = abs(float(inv["position_shares"]))
            if pos >= self.config.max_inventory_per_market:
                return RiskDecision(False, f"inventory limit for {token_id[:12]}... ({pos:.1f} shares)")

        return RiskDecision(True)
