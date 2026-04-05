import json

from polymarket_bot.polymarket import parse_market


def test_parse_market_handles_json_encoded_fields():
    raw = {
        "id": "123",
        "question": "Will Team A win?",
        "slug": "team-a-win",
        "liquidityNum": 1000,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["11", "22"]),
        "events": [{"title": "NBA Finals"}],
    }
    market = parse_market(raw)
    assert market.id == "123"
    assert market.outcomes == ["Yes", "No"]
    assert market.token_ids == ["11", "22"]
    assert market.event_title == "NBA Finals"
    assert market.enable_orderbook is True
    assert market.neg_risk is False


def test_parse_market_rejects_mismatched_outcomes_and_tokens():
    raw = {
        "id": "123",
        "question": "Will Team A win?",
        "slug": "team-a-win",
        "liquidityNum": 1000,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "outcomes": json.dumps(["Yes", "No", "Draw"]),
        "clobTokenIds": json.dumps(["11", "22"]),
    }
    assert parse_market(raw) is None


def test_parse_market_maps_enable_orderbook_and_neg_risk():
    raw = {
        "id": "999",
        "question": "Will Team A make playoffs?",
        "slug": "team-a-playoffs",
        "liquidityNum": 2000,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": "false",
        "negRisk": "true",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["100", "200"]),
    }
    market = parse_market(raw)
    assert market is not None
    assert market.enable_orderbook is False
    assert market.neg_risk is True

