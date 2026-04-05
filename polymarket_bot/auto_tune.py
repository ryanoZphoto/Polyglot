from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .config import BotConfig

if TYPE_CHECKING:
    from .runtime import RunSummary


@dataclass(frozen=True)
class TuningDecision:
    changed: bool
    reason: str
    new_config: BotConfig


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class AutoTuner:
    """
    Adaptive policy tuner.

    It tunes net filters and aggression within strict bounds based on recent cycle quality.
    """

    def __init__(self, config: BotConfig):
        self._base = config
        self._cycle_count = 0

    def tune(self, config: BotConfig, summary: RunSummary) -> TuningDecision:
        if not config.enable_auto_tune:
            return TuningDecision(False, "auto_tune_disabled", config)

        self._cycle_count += 1
        interval = max(1, config.auto_tune_interval_cycles)
        if self._cycle_count % interval != 0:
            return TuningDecision(False, "waiting_interval", config)

        best_net_profit = summary.top_scores[0].net_profit_usd if summary.top_scores else -1.0
        executed = len(summary.executions)
        selected = len(summary.opportunities)
        opportunities_found = (
            int(summary.diagnostics.get("no_basket_found", 0))
            + int(summary.diagnostics.get("pair_found", 0))
            + int(summary.diagnostics.get("multi_found", 0))
        )

        aggression = config.aggression
        min_profit = config.min_net_profit_usd
        min_edge = config.min_net_edge
        reason = "hold"

        if opportunities_found == 0:
            # No opportunities found at scanner level: loosen filters slightly.
            aggression += config.auto_tune_aggression_step
            min_profit -= config.auto_tune_profit_step
            min_edge -= config.auto_tune_edge_step
            reason = "loosen:no_opportunities"
        elif selected == 0 and best_net_profit >= 0:
            # We found candidates but allocator filtered all; loosen modestly.
            aggression += config.auto_tune_aggression_step * 0.8
            min_profit -= config.auto_tune_profit_step * 0.8
            min_edge -= config.auto_tune_edge_step * 0.8
            reason = "loosen:filtered_all"
        elif executed > 0 and best_net_profit > (config.min_net_profit_usd * 2.0):
            # If quality is strong and fills are happening, tighten slightly to keep quality.
            aggression -= config.auto_tune_aggression_step * 0.5
            min_profit += config.auto_tune_profit_step * 0.5
            min_edge += config.auto_tune_edge_step * 0.5
            reason = "tighten:strong_exec_quality"
        elif best_net_profit < 0:
            # Top setups are net negative after costs: tighten to avoid bad fills.
            aggression -= config.auto_tune_aggression_step
            min_profit += config.auto_tune_profit_step
            min_edge += config.auto_tune_edge_step
            reason = "tighten:negative_net_quality"

        new_aggression = _clamp(aggression, config.auto_tune_min_aggression, config.auto_tune_max_aggression)
        new_profit = _clamp(
            min_profit,
            config.auto_tune_min_net_profit_usd,
            config.auto_tune_max_net_profit_usd,
        )
        new_edge = _clamp(min_edge, config.auto_tune_min_net_edge, config.auto_tune_max_net_edge)

        changed = (
            abs(new_aggression - config.aggression) > 1e-12
            or abs(new_profit - config.min_net_profit_usd) > 1e-12
            or abs(new_edge - config.min_net_edge) > 1e-12
        )
        if not changed:
            return TuningDecision(False, f"no_change:{reason}", config)

        tuned = replace(
            config,
            aggression=new_aggression,
            min_net_profit_usd=new_profit,
            min_net_edge=new_edge,
        )
        return TuningDecision(True, reason, tuned)
