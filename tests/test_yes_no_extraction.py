from polymarket_bot.strategy import extract_no_leg
from polymarket_bot.types import OutcomeQuote, ParsedMarket


def test_extract_no_leg_for_binary_market():
    market = ParsedMarket(
        market_id="m1",
        question="Will Team A win?",
        slug="team-a-win",
        liquidity=10000.0,
        token_ids=["yes_token", "no_token"],
        outcomes=["Yes", "No"],
        best_asks=[
            OutcomeQuote(name="Yes", token_id="yes_token", best_ask=0.41),
            OutcomeQuote(name="No", token_id="no_token", best_ask=0.62),
        ],
    )
    leg = extract_no_leg(market)
    assert leg is not None
    assert leg.market_id == "m1"
    assert leg.price == 0.62


def test_extract_no_leg_rejects_non_binary_market():
    market = ParsedMarket(
        market_id="m2",
        question="Winner?",
        slug="winner",
        liquidity=10000.0,
        token_ids=["a", "b", "c"],
        outcomes=["A", "B", "C"],
        best_asks=[
            OutcomeQuote(name="A", token_id="a", best_ask=0.3),
            OutcomeQuote(name="B", token_id="b", best_ask=0.3),
            OutcomeQuote(name="C", token_id="c", best_ask=0.4),
        ],
    )
    assert extract_no_leg(market) is None
