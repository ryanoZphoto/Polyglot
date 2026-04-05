from __future__ import annotations

from .config import BotConfig
from .profit_scoring import ScoredOpportunity


class StrategyAllocator:
    def __init__(self, config: BotConfig):
        self.config = config

    def select(self, scored: list[ScoredOpportunity]) -> list[ScoredOpportunity]:
        if not scored:
            return []
        # Higher aggression means allowing more candidates through.
        aggression = max(0.0, min(1.0, self.config.aggression))
        dynamic_min_net_profit = self.config.min_net_profit_usd * (1.0 - (0.35 * aggression))
        dynamic_min_net_edge = self.config.min_net_edge * (1.0 - (0.30 * aggression))
        max_candidates = max(1, min(self.config.max_opportunities_per_cycle, int(1 + aggression * 3)))

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
