# Copy from event_trader/risk.py
from __future__ import annotations

import logging

from .config import EVConfig
from .state import EVStateStore

logger = logging.getLogger(__name__)


class EVRiskEngine:
    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    def can_enter(self, cost_usd: float, market_slug: str) -> tuple[bool, str]:
        positions = self.state.get_positions()
        
        total_exposure = sum(p.cost_usd for p in positions) + cost_usd
        if total_exposure > self.config.max_total_exposure_usd:
            return False, f"total_exposure={total_exposure:.2f} exceeds max={self.config.max_total_exposure_usd}"
        
        market_exposure = sum(p.cost_usd for p in positions if p.market_slug == market_slug) + cost_usd
        if market_exposure > self.config.max_per_market_usd:
            return False, f"market_exposure={market_exposure:.2f} exceeds max={self.config.max_per_market_usd}"
        
        if len(positions) >= self.config.max_positions:
            return False, f"position_count={len(positions)} at max={self.config.max_positions}"
        
        return True, "ok"