from __future__ import annotations

from dataclasses import dataclass

from .config import BotConfig
from .external_feeds import ExternalSignalClient, weighted_external_adjustment
from .types import Opportunity


@dataclass(frozen=True)
class ScoredOpportunity:
    opportunity: Opportunity
    adjusted_expected_profit_usd: float
    estimated_fee_usd: float
    estimated_slippage_usd: float
    estimated_latency_penalty_usd: float
    estimated_risk_buffer_usd: float
    net_profit_usd: float
    net_edge: float
    score: float


class ProfitScorer:
    def __init__(self, config: BotConfig):
        self.config = config
        self.external = ExternalSignalClient(config)

    def score_many(self, opportunities: list[Opportunity]) -> list[ScoredOpportunity]:
        scored = [self.score(opp) for opp in opportunities]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    def score(self, opp: Opportunity) -> ScoredOpportunity:
        gross_profit = max(0.0, opp.expected_profit_usd)
        fee_usd = self._estimate_fees_usd(opp)
        slippage_usd = self._estimate_slippage_usd(opp)
        latency_usd = self._estimate_latency_penalty_usd(opp)
        risk_buffer_usd = self._estimate_risk_buffer_usd(opp)
        external_adj = self._estimate_external_adjustment_usd(opp)
        adjusted_gross = gross_profit + external_adj
        net_profit = adjusted_gross - fee_usd - slippage_usd - latency_usd - risk_buffer_usd
        denom = max(opp.total_cost_usd, 1e-9)
        net_edge = net_profit / denom
        score = net_profit + (net_edge * 2.0)
        return ScoredOpportunity(
            opportunity=opp,
            adjusted_expected_profit_usd=adjusted_gross,
            estimated_fee_usd=fee_usd,
            estimated_slippage_usd=slippage_usd,
            estimated_latency_penalty_usd=latency_usd,
            estimated_risk_buffer_usd=risk_buffer_usd,
            net_profit_usd=net_profit,
            net_edge=net_edge,
            score=score,
        )

    def _estimate_fees_usd(self, opp: Opportunity) -> float:
        # Approximate taker fee: C * rate * p * (1-p) for each leg.
        # C is bundle shares and p is leg price.
        total = 0.0
        for leg in opp.legs:
            p = max(0.0, min(1.0, leg.price))
            total += opp.bundle_shares * self.config.taker_fee_rate * p * (1.0 - p)
        return max(0.0, total)

    def _estimate_slippage_usd(self, opp: Opportunity) -> float:
        illiq_component = 0.0
        if opp.min_leg_liquidity > 0:
            illiq_component = self.config.slippage_bps_illiquidity / opp.min_leg_liquidity
        bps = self.config.slippage_bps_base + (len(opp.legs) * self.config.slippage_bps_per_leg) + illiq_component
        return opp.total_cost_usd * max(0.0, bps) / 10_000.0

    def _estimate_latency_penalty_usd(self, opp: Opportunity) -> float:
        return opp.total_cost_usd * max(0.0, self.config.latency_penalty_bps) / 10_000.0

    def _estimate_risk_buffer_usd(self, opp: Opportunity) -> float:
        return opp.total_cost_usd * max(0.0, self.config.risk_buffer_bps) / 10_000.0

    def _estimate_external_adjustment_usd(self, opp: Opportunity) -> float:
        if not opp.legs:
            return 0.0
        signal = self.external.signal_for_market_question(opp.legs[0].market_question)
        if signal is None:
            return 0.0
        market_prob = max(0.0, min(1.0, opp.sum_ask / max(1, len(opp.legs))))
        delta_prob = weighted_external_adjustment(signal, market_prob)
        return delta_prob * opp.bundle_shares
