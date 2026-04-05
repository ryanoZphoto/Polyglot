from polymarket_bot.config import BotConfig
from polymarket_bot.risk import RiskEngine
from polymarket_bot.state import StateStore
from polymarket_bot.types import MarketLeg, Opportunity


def _build_config(**overrides) -> BotConfig:
    base = BotConfig.from_env()
    values = {**base.__dict__, **overrides}
    return BotConfig(**values)


def _build_opportunity(group_key: str = "event:group", cost: float = 100.0) -> Opportunity:
    leg = MarketLeg(
        market_id="m1",
        market_slug="m1",
        market_question="Q",
        token_id="t1",
        outcome_name="No",
        price=0.5,
        liquidity=10000,
    )
    return Opportunity(
        group_key=group_key,
        legs=[leg],
        sum_ask=0.5,
        edge=0.5,
        bundle_shares=cost / 0.5,
        bundle_cost=cost,
        guaranteed_payout_usd=cost * 1.2,
        expected_profit_usd=cost * 0.2,
        min_leg_liquidity=10000,
    )


def test_risk_blocks_emergency_stop(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(str(db))
    try:
        config = _build_config(emergency_stop=True, state_db_path=str(db))
        decision = RiskEngine(config, store).evaluate(_build_opportunity())
        assert decision.allowed is False
        assert "emergency stop" in (decision.reason or "")
    finally:
        store.close()


def test_risk_blocks_event_exposure_limit(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(str(db))
    try:
        config = _build_config(
            max_event_exposure_usd=120.0,
            max_open_exposure_usd=10000.0,
            state_db_path=str(db),
        )
        store.record_trade(
            trade_id="existing",
            cycle_id="c1",
            group_key="event:group",
            event_key="event:group",
            mode="live",
            status="submitted",
            cost_usd=80.0,
            expected_profit_usd=5.0,
        )
        decision = RiskEngine(config, store).evaluate(_build_opportunity(cost=50.0))
        assert decision.allowed is False
        assert "event exposure" in (decision.reason or "")
    finally:
        store.close()
