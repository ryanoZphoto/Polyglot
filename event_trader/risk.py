from __future__ import annotations

from .config import EVConfig
from .state import EVStateStore
from .types import RiskDecision


class EVRiskEngine:
    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    def pre_cycle_check(self) -> RiskDecision:
        """Global checks before any scanning or trading."""
        if self.config.emergency_stop:
            return RiskDecision(False, "emergency stop active")

        daily_pnl = self.state.daily_realized_pnl()
        if daily_pnl < -self.config.max_daily_loss_usd:
            return RiskDecision(False, f"daily loss limit: ${daily_pnl:.2f}")

        exposure = self.state.total_exposure_usd()
        if exposure >= self.config.max_total_exposure_usd:
            return RiskDecision(False, f"total exposure at limit: ${exposure:.2f}")

        return RiskDecision(True)

    def pre_entry_check(self, market_id: str, token_id: str,
                        entry_cost_usd: float) -> RiskDecision:
        """Check whether we can open a new position."""
        if self.config.emergency_stop:
            return RiskDecision(False, "emergency stop")

        open_count = self.state.count_open_positions()
        if open_count >= self.config.max_positions:
            return RiskDecision(False, f"max positions ({open_count}/{self.config.max_positions})")

        if self.state.has_position_for_token(token_id):
            return RiskDecision(False, "already have position for this token")

        exposure = self.state.total_exposure_usd()
        if exposure + entry_cost_usd > self.config.max_total_exposure_usd:
            return RiskDecision(
                False,
                f"would exceed exposure limit: ${exposure:.2f} + ${entry_cost_usd:.2f} > ${self.config.max_total_exposure_usd:.2f}",
            )

        market_exposure = self._market_exposure(market_id)
        if market_exposure + entry_cost_usd > self.config.max_per_market_usd:
            return RiskDecision(
                False,
                f"would exceed per-market limit: ${market_exposure:.2f} + ${entry_cost_usd:.2f}",
            )

        return RiskDecision(True)

    def _market_exposure(self, market_id: str) -> float:
        positions = self.state.get_open_positions()
        total = 0.0
        for p in positions:
            if p["market_id"] == market_id:
                total += float(p["contracts"]) * float(p["current_price"])
        return total
