from __future__ import annotations

from .config import BotConfig
from .state import StateStore
from .types import Opportunity, RiskDecision


class RiskEngine:
    def __init__(self, config: BotConfig, state: StateStore):
        self.config = config
        self.state = state

    def evaluate(self, opp: Opportunity) -> RiskDecision:
        if self.config.emergency_stop:
            return RiskDecision(False, "blocked: emergency stop enabled")

        snapshot = self.state.exposure_snapshot()
        if snapshot.daily_realized_pnl_usd <= -abs(self.config.max_daily_loss_usd):
            return RiskDecision(False, "blocked: max daily loss exceeded")

        projected_open = snapshot.open_exposure_usd + opp.total_cost_usd
        if projected_open > self.config.max_open_exposure_usd:
            return RiskDecision(False, "blocked: max open exposure exceeded")

        event_exposure = self.state.event_open_exposure(opp.group_key) + opp.total_cost_usd
        if event_exposure > self.config.max_event_exposure_usd:
            return RiskDecision(False, "blocked: max event exposure exceeded")

        orders_last_min = self.state.count_orders_last_minute()
        projected_orders = orders_last_min + len(opp.legs)
        if projected_orders > self.config.max_orders_per_minute:
            return RiskDecision(False, "blocked: order-rate limit exceeded")

        return RiskDecision(True, None)
