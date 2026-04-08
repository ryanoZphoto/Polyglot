from __future__ import annotations

from .config import BotConfig
from .profit_scoring import ScoredOpportunity


class StrategyAllocator:
    def __init__(self, config: BotConfig):
        self.config = config

    def select(self, scored: list[ScoredOpportunity]) -> list[ScoredOpportunity]:
        if not scored:
            return []
        # Issue E fix: aggression scaling was too weak (only 35%/30% reduction at full aggression).
        # New formula: aggression=0.5 reduces thresholds by 50%; aggression=1.0 drops them to 0.
        # This allows the auto-tuner to actually reach candidates when loosening.
        aggression = max(0.0, min(1.0, self.config.aggression))
        dynamic_min_net_profit = self.config.min_net_profit_usd * max(0.0, 1.0 - aggression)
        dynamic_min_net_edge = self.config.min_net_edge * max(0.0, 1.0 - aggression)
        max_candidates = max(1, min(self.config.max_opportunities_per_cycle, int(1 + aggression * 5)))

        selected: list[ScoredOpportunity] = []
        seen_groups: set[str] = set()
        for item in scored:
            if item.opportunity.group_key in seen_groups:
                continue
            if item.net_profit_usd < dynamic_min_net_profit:
                continue
            if item.net_edge < dynamic_min_net_edge:
                continue
            selected.append(item)
            seen_groups.add(item.opportunity.group_key)
            if len(selected) >= max_candidates:
                break
        return selected
