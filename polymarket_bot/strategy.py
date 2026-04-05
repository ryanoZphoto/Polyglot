from __future__ import annotations

from .types import MarketLeg, Opportunity, ParsedMarket


def _extract_no_leg_from_parts(
    market_id: str,
    market_slug: str,
    market_question: str,
    outcomes: list[str],
    token_ids: list[str],
    asks: list[float | None],
    liquidity: float,
) -> MarketLeg | None:
    if len(outcomes) != 2 or len(token_ids) != 2 or len(asks) != 2:
        return None
    no_idx = None
    for idx, outcome in enumerate(outcomes):
        if outcome.strip().lower() == "no":
            no_idx = idx
            break
    if no_idx is None:
        return None
    price = asks[no_idx]
    if price is None or price <= 0 or price >= 1:
        return None
    return MarketLeg(
        market_id=market_id,
        market_slug=market_slug,
        market_question=market_question,
        token_id=token_ids[no_idx],
        outcome_name=outcomes[no_idx],
        price=price,
        liquidity=liquidity,
    )


def extract_no_leg(market: ParsedMarket) -> MarketLeg | None:
    asks = [q.best_ask for q in market.best_asks]
    return _extract_no_leg_from_parts(
        market_id=market.market_id,
        market_slug=market.slug,
        market_question=market.question,
        outcomes=market.outcomes,
        token_ids=market.token_ids,
        asks=asks,
        liquidity=market.liquidity,
    )


def find_no_basket_arbitrage(
    group_key: str,
    legs: list[MarketLeg],
    min_group_size: int,
    max_group_size: int,
    min_edge: float,
    min_profit_usd: float,
    max_capital: float,
    max_bundle_shares: float,
) -> Opportunity | None:
    if len(legs) < min_group_size:
        return None

    sorted_legs = sorted(legs, key=lambda leg: leg.price)
    max_n = min(max_group_size, len(sorted_legs))
    if max_n < min_group_size:
        return None

    best: Opportunity | None = None
    running_sum = 0.0
    for n in range(1, max_n + 1):
        running_sum += sorted_legs[n - 1].price
        if n < min_group_size:
            continue

        candidate = sorted_legs[:n]
        sum_ask = running_sum
        guaranteed_payout_per_share = float(n - 1)
        edge = guaranteed_payout_per_share - sum_ask
        if edge < min_edge:
            continue
        if sum_ask <= 0:
            continue

        max_shares_by_capital = max_capital / sum_ask
        bundle_shares = min(max_bundle_shares, max_shares_by_capital)
        if bundle_shares <= 0:
            continue

        bundle_cost = sum_ask * bundle_shares
        guaranteed_payout = guaranteed_payout_per_share * bundle_shares
        expected_profit = guaranteed_payout - bundle_cost
        if expected_profit < min_profit_usd:
            continue

        opp = Opportunity(
            group_key=group_key,
            legs=candidate,
            sum_ask=sum_ask,
            edge=edge,
            bundle_shares=bundle_shares,
            bundle_cost=bundle_cost,
            guaranteed_payout_usd=guaranteed_payout,
            expected_profit_usd=expected_profit,
            min_leg_liquidity=min(leg.liquidity for leg in candidate),
        )
        if best is None or opp.expected_profit_usd > best.expected_profit_usd:
            best = opp

    return best
