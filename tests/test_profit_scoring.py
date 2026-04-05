from polymarket_bot.config import BotConfig
from polymarket_bot.profit_scoring import ProfitScorer
from polymarket_bot.strategy_allocator import StrategyAllocator
from polymarket_bot.types import MarketLeg, Opportunity


def _build_config(**overrides) -> BotConfig:
    base = BotConfig.from_env()
    values = {**base.__dict__, **overrides}
    return BotConfig(**values)


def _build_opp(expected_profit: float, cost: float = 10.0, legs_count: int = 2) -> Opportunity:
    legs = [
        MarketLeg(
            market_id=f"m{i}",
            market_slug=f"m{i}",
            market_question="Sample question",
            token_id=f"t{i}",
            outcome_name="No",
            price=0.45,
            liquidity=10_000.0,
        )
        for i in range(legs_count)
    ]
    return Opportunity(
        group_key=f"group:{expected_profit}:{cost}:{legs_count}",
        legs=legs,
        sum_ask=sum(leg.price for leg in legs),
        edge=0.1,
        bundle_shares=cost / max(0.01, sum(leg.price for leg in legs)),
        bundle_cost=cost,
        guaranteed_payout_usd=cost * 1.1,
        expected_profit_usd=expected_profit,
        min_leg_liquidity=10_000.0,
    )


def test_profit_scorer_net_profit_penalizes_costs():
    config = _build_config(
        taker_fee_rate=0.03,
        slippage_bps_base=5.0,
        slippage_bps_per_leg=2.0,
        latency_penalty_bps=4.0,
        risk_buffer_bps=8.0,
        enable_external_price_check=False,
    )
    scorer = ProfitScorer(config)
    opp = _build_opp(expected_profit=1.0, cost=10.0, legs_count=2)
    scored = scorer.score(opp)
    assert scored.net_profit_usd < scored.adjusted_expected_profit_usd
    assert scored.net_edge < (opp.expected_profit_usd / opp.total_cost_usd)


def test_allocator_filters_negative_net_and_keeps_best():
    config = _build_config(
        aggression=0.9,
        max_opportunities_per_cycle=3,
        min_net_profit_usd=0.15,
        min_net_edge=0.003,
        enable_external_price_check=False,
    )
    scorer = ProfitScorer(config)
    allocator = StrategyAllocator(config)

    high = scorer.score(_build_opp(expected_profit=1.2, cost=10.0))
    low = scorer.score(_build_opp(expected_profit=0.01, cost=10.0))
    selected = allocator.select([low, high])
    assert selected
    assert selected[0].score >= selected[-1].score
    assert all(item.net_profit_usd >= 0 for item in selected)
