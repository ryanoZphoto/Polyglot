import pytest

from polymarket_bot.strategy import find_no_basket_arbitrage
from polymarket_bot.types import MarketLeg


def build_legs() -> list[MarketLeg]:
    return [
        MarketLeg(
            market_id="m1",
            market_slug="team-a-title",
            market_question="Will Team A win title?",
            token_id="no_a",
            outcome_name="No",
            price=0.44,
            liquidity=12000.0,
        ),
        MarketLeg(
            market_id="m2",
            market_slug="team-b-title",
            market_question="Will Team B win title?",
            token_id="no_b",
            outcome_name="No",
            price=0.47,
            liquidity=11500.0,
        ),
        MarketLeg(
            market_id="m3",
            market_slug="team-c-title",
            market_question="Will Team C win title?",
            token_id="no_c",
            outcome_name="No",
            price=0.52,
            liquidity=9000.0,
        ),
    ]


def test_find_no_basket_arbitrage_returns_opportunity():
    opp = find_no_basket_arbitrage(
        group_key="league:title_winner",
        legs=build_legs(),
        min_group_size=2,
        max_group_size=3,
        min_edge=0.01,
        min_profit_usd=1.0,
        max_capital=100.0,
        max_bundle_shares=500.0,
    )
    assert opp is not None
    assert len(opp.legs) == 3
    assert opp.edge == pytest.approx(0.57, rel=1e-9)
    assert opp.sum_best_asks == pytest.approx(1.43, rel=1e-9)
    assert opp.total_cost_usd == pytest.approx(100.0, rel=1e-9)
    assert opp.guaranteed_payout_usd == pytest.approx(139.86013986013987, rel=1e-9)
    assert opp.guaranteed_profit_usd == pytest.approx(39.86013986013987, rel=1e-9)


def test_find_no_basket_arbitrage_respects_min_edge():
    opp = find_no_basket_arbitrage(
        group_key="league:title_winner",
        legs=build_legs(),
        min_group_size=2,
        max_group_size=3,
        min_edge=0.8,
        min_profit_usd=1.0,
        max_capital=100.0,
        max_bundle_shares=500.0,
    )
    assert opp is None


def test_find_no_basket_arbitrage_none_when_not_enough_legs():
    opp = find_no_basket_arbitrage(
        group_key="league:title_winner",
        legs=build_legs()[:1],
        min_group_size=2,
        max_group_size=3,
        min_edge=0.01,
        min_profit_usd=0.1,
        max_capital=100.0,
        max_bundle_shares=500.0,
    )
    assert opp is None

