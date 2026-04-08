from polymarket_bot.auto_tune import AutoTuner
from polymarket_bot.config import BotConfig
from polymarket_bot.profit_scoring import ScoredOpportunity
from polymarket_bot.runtime import RunSummary
from polymarket_bot.types import ExecutionResult, MarketLeg, Opportunity


def _build_config(**overrides) -> BotConfig:
    base = BotConfig.from_env()
    values = {**base.__dict__, **overrides}
    return BotConfig(**values)


def _opp() -> Opportunity:
    leg = MarketLeg(
        market_id="m1",
        market_slug="m1",
        market_question="Q",
        token_id="t1",
        outcome_name="No",
        price=0.4,
        liquidity=10000.0,
    )
    return Opportunity(
        group_key="g1",
        legs=[leg],
        sum_ask=0.4,
        edge=0.1,
        bundle_shares=10.0,
        bundle_cost=4.0,
        guaranteed_payout_usd=5.0,
        expected_profit_usd=1.0,
        min_leg_liquidity=10000.0,
    )


def _score(net_profit: float, net_edge: float) -> ScoredOpportunity:
    opp = _opp()
    return ScoredOpportunity(
        opportunity=opp,
        adjusted_expected_profit_usd=opp.expected_profit_usd,
        estimated_fee_usd=0.0,
        estimated_slippage_usd=0.0,
        estimated_latency_penalty_usd=0.0,
        estimated_risk_buffer_usd=0.0,
        net_profit_usd=net_profit,
        net_edge=net_edge,
        score=net_profit + net_edge,
    )


def _summary(top_scores: list[ScoredOpportunity], opportunities: int, executions: int, found: int) -> RunSummary:
    return RunSummary(
        cycle_id="c1",
        scanned_markets=100,
        eligible_groups=20,
        opportunities=[_opp() for _ in range(opportunities)],
        executions=[ExecutionResult(ok=True, mode="live", message="ok") for _ in range(executions)],
        near_misses=[],
        diagnostics={
            "no_basket_found": found,
            "pair_found": 0,
            "multi_found": 0,
        },
        top_scores=top_scores,
    )


def test_auto_tuner_tightens_on_negative_net_quality():
    config = _build_config(
        enable_auto_tune=True,
        auto_tune_interval_cycles=1,
        aggression=0.9,
        min_net_profit_usd=0.2,
        min_net_edge=0.004,
        auto_tune_min_aggression=0.55,
        auto_tune_max_aggression=0.97,
    )
    tuner = AutoTuner(config)
    summary = _summary(top_scores=[_score(-0.2, -0.01)], opportunities=0, executions=0, found=3)
    decision = tuner.tune(config, summary)
    assert decision.changed is True
    assert decision.new_config.aggression < config.aggression
    assert decision.new_config.min_net_profit_usd > config.min_net_profit_usd
    assert decision.new_config.min_net_edge > config.min_net_edge


def test_auto_tuner_loosens_when_filtered_out():
    config = _build_config(
        enable_auto_tune=True,
        auto_tune_interval_cycles=1,
        aggression=0.8,
        min_net_profit_usd=0.2,
        min_net_edge=0.004,
    )
    tuner = AutoTuner(config)
    summary = _summary(top_scores=[_score(0.18, 0.0038)], opportunities=0, executions=0, found=2)
    decision = tuner.tune(config, summary)
    assert decision.changed is True
    assert decision.new_config.aggression > config.aggression
    assert decision.new_config.min_net_profit_usd < config.min_net_profit_usd
    assert decision.new_config.min_net_edge < config.min_net_edge
