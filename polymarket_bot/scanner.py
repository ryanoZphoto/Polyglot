from __future__ import annotations

import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from .config import BotConfig
from .data_client import ResilientDataClient
from .polymarket import extract_no_quote
from .strategy import find_no_basket_arbitrage
from .types import MarketLeg, Opportunity, ParsedMarket


@dataclass(frozen=True)
class ScanResult:
    scanned_markets: int
    grouped_candidates: int
    opportunities: list[Opportunity]
    near_misses: list["NearMissCandidate"]
    diagnostics: dict[str, int]


@dataclass(frozen=True)
class NearMissCandidate:
    group_key: str
    legs_considered: int
    sum_ask: float
    payout_per_share: float
    edge: float
    min_edge_required: float
    edge_gap: float
    estimated_profit_usd: float
    min_profit_required: float
    profit_gap_usd: float


def _group_key(market: ParsedMarket) -> str | None:
    if not market.event_slug:
        return None
    # Group at event level to avoid over-fragmenting candidates into single-market buckets.
    return market.event_slug.lower()


def _market_is_eligible(market: ParsedMarket, config: BotConfig, last_trade_at: dict[str, float]) -> bool:
    if not market.active or market.closed:
        return False
    if not market.accepting_orders:
        return False
    if not market.enable_orderbook:
        return False
    if market.liquidity < config.min_liquidity:
        return False
    if len(market.outcomes) < 2:
        return False

    if config.sports_only and not market.is_sports:
        return False

    if config.include_keywords:
        text = f"{market.question} {market.event_title or ''}".lower()
        if not any(k in text for k in config.include_keywords):
            return False

    last = last_trade_at.get(market.id)
    if last is not None and (time.time() - last) < config.market_cooldown_seconds:
        return False

    return True


class OpportunityScanner:
    def __init__(self, config: BotConfig, client: ResilientDataClient):
        self.config = config
        self.client = client

    def scan(self, last_trade_at: dict[str, float]) -> tuple[ScanResult, list[ParsedMarket]]:
        """
        Returns (ScanResult, fetched_markets) so the caller can reuse the market list
        for other strategies (e.g. leader-follow) without a second API fetch (Issue F fix).
        """
        markets = self.client.fetch_active_markets(self.config.scan_limit)

        eligible = [m for m in markets if _market_is_eligible(m, self.config, last_trade_at)]

        # Issue #2 fix: Collect ALL tokens across all active strategies to fetch in one parallel pass.
        tokens_to_fetch: set[str] = set()
        for m in eligible:
            if self.config.enable_no_basket_strategy:
                nq = extract_no_quote(m)
                if nq:
                    tokens_to_fetch.add(nq.token_id)

            if self.config.enable_binary_pair_strategy and len(m.token_ids) == 2:
                tokens_to_fetch.update(m.token_ids)

            if self.config.enable_multi_outcome_strategy and len(m.token_ids) >= 3:
                tokens_to_fetch.update(m.token_ids)

        quotes_map: dict[str, float] = {}
        if tokens_to_fetch:
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                results = list(executor.map(self.client.fetch_best_ask, tokens_to_fetch))
                for q in results:
                    if q.best_ask is not None:
                        quotes_map[q.token_id] = q.best_ask

        legs_with_quotes: list[MarketLeg] = []
        # Build a fast lookup from market_id → market for grouping step.
        market_by_id: dict[str, ParsedMarket] = {m.market_id: m for m in eligible}
        for m in eligible:
            nq = extract_no_quote(m)
            if nq and nq.token_id in quotes_map:
                legs_with_quotes.append(MarketLeg(
                    market_id=m.market_id,
                    market_slug=m.slug,
                    market_question=m.question,
                    token_id=nq.token_id,
                    outcome_name=nq.name,
                    price=quotes_map[nq.token_id],
                    liquidity=m.liquidity,
                ))

        grouped: dict[str, list[MarketLeg]] = {}
        for leg in legs_with_quotes:
            m_orig = market_by_id.get(leg.market_id)
            if m_orig is None:
                continue
            key = _group_key(m_orig)
            if key:
                grouped.setdefault(key, []).append(leg)

        diagnostics: dict[str, int] = {
            "eligible_markets": len(eligible),
            "groups_total": len(grouped),
            "groups_below_min_size": 0,
            "no_basket_evaluated": 0,
            "no_basket_found": 0,
            "no_basket_best_edge_ppm": -1000000,
            "no_basket_best_profit_cents": -1000000,
            "pair_evaluated": 0,
            "pair_found": 0,
            "pair_best_edge_ppm": -1000000,
            "pair_best_profit_cents": -1000000,
            "multi_evaluated": 0,
            "multi_found": 0,
            "multi_best_edge_ppm": -1000000,
            "multi_best_profit_cents": -1000000,
        }

        opportunities: list[Opportunity] = []
        near_misses: list[NearMissCandidate] = []
        diagnostics["groups_total"] = len(grouped)
        for key, legs in grouped.items():
            if len(legs) < self.config.min_group_size:
                diagnostics["groups_below_min_size"] += 1
                continue
            if self.config.enable_no_basket_strategy:
                diagnostics["no_basket_evaluated"] += 1
                signal = self._best_no_basket_signal(legs)
                if signal is not None:
                    best_edge, best_profit = signal
                    diagnostics["no_basket_best_edge_ppm"] = max(
                        diagnostics["no_basket_best_edge_ppm"],
                        int(best_edge * 1_000_000),
                    )
                    diagnostics["no_basket_best_profit_cents"] = max(
                        diagnostics["no_basket_best_profit_cents"],
                        int(best_profit * 100),
                    )
                opp = find_no_basket_arbitrage(
                    group_key=key,
                    legs=legs,
                    min_group_size=self.config.min_group_size,
                    max_group_size=self.config.max_group_size,
                    min_edge=self.config.min_edge,
                    min_profit_usd=self.config.min_profit_usd,
                    max_capital=self.config.max_capital_per_trade,
                    max_bundle_shares=self.config.max_bundle_shares,
                )
                if opp:
                    opportunities.append(opp)
                    diagnostics["no_basket_found"] += 1
                    continue

                candidate = self._best_near_miss(key, legs)
                if candidate is not None:
                    near_misses.append(candidate)

        if self.config.enable_binary_pair_strategy:
            (
                pair_opps,
                pair_near_misses,
                pair_eval,
                pair_found,
                pair_best_edge,
                pair_best_profit,
            ) = self._scan_binary_pair_opportunities(eligible, quotes_map)
            opportunities.extend(pair_opps)
            near_misses.extend(pair_near_misses)
            diagnostics["pair_evaluated"] += pair_eval
            diagnostics["pair_found"] += pair_found
            diagnostics["pair_best_edge_ppm"] = max(
                diagnostics["pair_best_edge_ppm"],
                int(pair_best_edge * 1_000_000),
            )
            diagnostics["pair_best_profit_cents"] = max(
                diagnostics["pair_best_profit_cents"],
                int(pair_best_profit * 100),
            )
        if self.config.enable_multi_outcome_strategy:
            (
                multi_opps,
                multi_near_misses,
                multi_eval,
                multi_found,
                multi_best_edge,
                multi_best_profit,
            ) = self._scan_multi_outcome_opportunities(eligible, quotes_map)
            opportunities.extend(multi_opps)
            near_misses.extend(multi_near_misses)
            diagnostics["multi_evaluated"] += multi_eval
            diagnostics["multi_found"] += multi_found
            diagnostics["multi_best_edge_ppm"] = max(
                diagnostics["multi_best_edge_ppm"],
                int(multi_best_edge * 1_000_000),
            )
            diagnostics["multi_best_profit_cents"] = max(
                diagnostics["multi_best_profit_cents"],
                int(multi_best_profit * 100),
            )

        opportunities.sort(key=lambda x: x.expected_profit_usd, reverse=True)
        near_misses.sort(key=lambda x: (x.edge_gap + x.profit_gap_usd, x.sum_ask))
        result = ScanResult(
            scanned_markets=len(markets),
            grouped_candidates=len(grouped),
            opportunities=opportunities[: self.config.max_opportunities_per_cycle],
            near_misses=near_misses[:8],
            diagnostics=diagnostics,
        )
        return result, markets

    def _best_no_basket_signal(self, legs: list[MarketLeg]) -> tuple[float, float] | None:
        """
        For a NO-basket of n mutually exclusive markets:
          guaranteed payout per share = (n - 1)  [n-1 NOs resolve winning, 1 loses]
          edge = (n-1) - sum_of_n_NO_asks
        We try all valid n from min_group_size up to max_group_size, taking the cheapest legs first.
        """
        sorted_legs = sorted(legs, key=lambda leg: leg.price)
        max_n = min(self.config.max_group_size, len(sorted_legs))
        if max_n < self.config.min_group_size:
            return None
        running_sum = 0.0
        best_edge = float("-inf")
        best_profit = float("-inf")
        for n in range(1, max_n + 1):
            running_sum += sorted_legs[n - 1].price
            if n < self.config.min_group_size:
                continue
            if running_sum <= 0:
                continue
            payout_per_share = float(n - 1)
            edge = payout_per_share - running_sum
            max_shares_by_capital = self.config.max_capital_per_trade / running_sum
            bundle_shares = min(self.config.max_bundle_shares, max_shares_by_capital)
            if bundle_shares <= 0:
                continue
            estimated_profit = edge * bundle_shares
            best_edge = max(best_edge, edge)
            best_profit = max(best_profit, estimated_profit)
        if best_edge == float("-inf"):
            return None
        return best_edge, best_profit

    def _scan_binary_pair_opportunities(
        self, markets: list[ParsedMarket], quotes_map: dict[str, float]
    ) -> tuple[list[Opportunity], list[NearMissCandidate], int, int, float, float]:
        opportunities: list[Opportunity] = []
        near_misses: list[NearMissCandidate] = []
        evaluated = 0
        found = 0
        best_edge = float("-inf")
        best_profit = float("-inf")
        for market in markets:
            if len(market.outcomes) != 2 or len(market.token_ids) != 2:
                continue

            yes_idx = None
            no_idx = None
            for idx, outcome in enumerate(market.outcomes):
                lowered = outcome.strip().lower()
                if lowered == "yes":
                    yes_idx = idx
                elif lowered == "no":
                    no_idx = idx
            if yes_idx is None or no_idx is None:
                continue

            evaluated += 1
            yes_ask = quotes_map.get(market.token_ids[yes_idx])
            no_ask = quotes_map.get(market.token_ids[no_idx])
            if yes_ask is None or no_ask is None:
                continue

            sum_ask = yes_ask + no_ask
            payout = 1.0
            edge = payout - sum_ask
            if sum_ask <= 0:
                continue
            max_shares_by_capital = self.config.max_capital_per_trade / sum_ask
            bundle_shares = min(self.config.max_bundle_shares, max_shares_by_capital)
            if bundle_shares <= 0:
                continue
            expected_profit = edge * bundle_shares
            best_edge = max(best_edge, edge)
            best_profit = max(best_profit, expected_profit)

            pair_key = f"pair:{market.market_id}"
            if edge >= self.config.pair_min_edge and expected_profit >= self.config.pair_min_profit_usd:
                yes_leg = MarketLeg(
                    market_id=market.market_id,
                    market_slug=market.slug,
                    market_question=market.question,
                    token_id=market.token_ids[yes_idx],
                    outcome_name=market.outcomes[yes_idx],
                    price=yes_ask,
                    liquidity=market.liquidity,
                )
                no_leg = MarketLeg(
                    market_id=market.market_id,
                    market_slug=market.slug,
                    market_question=market.question,
                    token_id=market.token_ids[no_idx],
                    outcome_name=market.outcomes[no_idx],
                    price=no_ask,
                    liquidity=market.liquidity,
                )
                opportunities.append(
                    Opportunity(
                        group_key=pair_key,
                        legs=[yes_leg, no_leg],
                        sum_ask=sum_ask,
                        edge=edge,
                        bundle_shares=bundle_shares,
                        bundle_cost=sum_ask * bundle_shares,
                        guaranteed_payout_usd=bundle_shares,
                        expected_profit_usd=expected_profit,
                        min_leg_liquidity=min(yes_leg.liquidity, no_leg.liquidity),
                    )
                )
                found += 1
            else:
                near_misses.append(
                    NearMissCandidate(
                        group_key=pair_key,
                        legs_considered=2,
                        sum_ask=sum_ask,
                        payout_per_share=payout,
                        edge=edge,
                        min_edge_required=self.config.pair_min_edge,
                        edge_gap=max(0.0, self.config.pair_min_edge - edge),
                        estimated_profit_usd=expected_profit,
                        min_profit_required=self.config.pair_min_profit_usd,
                        profit_gap_usd=max(0.0, self.config.pair_min_profit_usd - expected_profit),
                    )
                )

        near_misses.sort(key=lambda x: (x.edge_gap + x.profit_gap_usd, x.sum_ask))
        if best_edge == float("-inf"):
            best_edge = -1.0
        if best_profit == float("-inf"):
            best_profit = -1.0
        return opportunities, near_misses[:6], evaluated, found, best_edge, best_profit

    def _scan_multi_outcome_opportunities(
        self, markets: list[ParsedMarket], quotes_map: dict[str, float]
    ) -> tuple[list[Opportunity], list[NearMissCandidate], int, int, float, float]:
        opportunities: list[Opportunity] = []
        near_misses: list[NearMissCandidate] = []
        evaluated = 0
        found = 0
        best_edge = float("-inf")
        best_profit = float("-inf")

        for market in markets:
            if len(market.token_ids) < 3:
                continue
            evaluated += 1

            legs: list[MarketLeg] = []
            sum_ask = 0.0
            invalid = False
            for token_id, outcome in zip(market.token_ids, market.outcomes):
                ask = quotes_map.get(token_id)
                if ask is None:
                    invalid = True
                    break
                sum_ask += ask
                legs.append(
                    MarketLeg(
                        market_id=market.market_id,
                        market_slug=market.slug,
                        market_question=market.question,
                        token_id=token_id,
                        outcome_name=outcome,
                        price=ask,
                        liquidity=market.liquidity,
                    )
                )
            if invalid or not legs or sum_ask <= 0:
                continue

            payout = 1.0
            edge = payout - sum_ask
            max_shares_by_capital = self.config.max_capital_per_trade / sum_ask
            bundle_shares = min(self.config.max_bundle_shares, max_shares_by_capital)
            if bundle_shares <= 0:
                continue
            expected_profit = edge * bundle_shares
            best_edge = max(best_edge, edge)
            best_profit = max(best_profit, expected_profit)

            group_key = f"multi:{market.market_id}"
            if edge >= self.config.multi_min_edge and expected_profit >= self.config.multi_min_profit_usd:
                opportunities.append(
                    Opportunity(
                        group_key=group_key,
                        legs=legs,
                        sum_ask=sum_ask,
                        edge=edge,
                        bundle_shares=bundle_shares,
                        bundle_cost=sum_ask * bundle_shares,
                        guaranteed_payout_usd=bundle_shares,
                        expected_profit_usd=expected_profit,
                        min_leg_liquidity=min(leg.liquidity for leg in legs),
                    )
                )
                found += 1
            else:
                near_misses.append(
                    NearMissCandidate(
                        group_key=group_key,
                        legs_considered=len(legs),
                        sum_ask=sum_ask,
                        payout_per_share=payout,
                        edge=edge,
                        min_edge_required=self.config.multi_min_edge,
                        edge_gap=max(0.0, self.config.multi_min_edge - edge),
                        estimated_profit_usd=expected_profit,
                        min_profit_required=self.config.multi_min_profit_usd,
                        profit_gap_usd=max(0.0, self.config.multi_min_profit_usd - expected_profit),
                    )
                )

        near_misses.sort(key=lambda x: (x.edge_gap + x.profit_gap_usd, x.sum_ask))
        if best_edge == float("-inf"):
            best_edge = -1.0
        if best_profit == float("-inf"):
            best_profit = -1.0
        return opportunities, near_misses[:6], evaluated, found, best_edge, best_profit

    def _best_near_miss(self, group_key: str, legs: list[MarketLeg]) -> NearMissCandidate | None:
        sorted_legs = sorted(legs, key=lambda leg: leg.price)
        max_n = min(self.config.max_group_size, len(sorted_legs))
        if max_n < self.config.min_group_size:
            return None

        best: NearMissCandidate | None = None
        running_sum = 0.0
        for n in range(1, max_n + 1):
            running_sum += sorted_legs[n - 1].price
            if n < self.config.min_group_size:
                continue
            if running_sum <= 0:
                continue

            payout_per_share = float(n - 1)
            edge = payout_per_share - running_sum
            max_shares_by_capital = self.config.max_capital_per_trade / running_sum
            bundle_shares = min(self.config.max_bundle_shares, max_shares_by_capital)
            if bundle_shares <= 0:
                continue
            estimated_profit = edge * bundle_shares
            edge_gap = max(0.0, self.config.min_edge - edge)
            profit_gap = max(0.0, self.config.min_profit_usd - estimated_profit)
            candidate = NearMissCandidate(
                group_key=group_key,
                legs_considered=n,
                sum_ask=running_sum,
                payout_per_share=payout_per_share,
                edge=edge,
                min_edge_required=self.config.min_edge,
                edge_gap=edge_gap,
                estimated_profit_usd=estimated_profit,
                min_profit_required=self.config.min_profit_usd,
                profit_gap_usd=profit_gap,
            )
            if best is None:
                best = candidate
                continue
            current_score = edge_gap + profit_gap
            best_score = best.edge_gap + best.profit_gap_usd
            if current_score < best_score:
                best = candidate
        return best
