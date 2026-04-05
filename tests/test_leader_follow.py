from polymarket_bot.config import BotConfig
from polymarket_bot.leader_follow import LeaderFollowStrategy
from polymarket_bot.types import OutcomeQuote, ParsedMarket


def _build_config(**overrides) -> BotConfig:
    base = BotConfig.from_env()
    values = {**base.__dict__, **overrides}
    return BotConfig(**values)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *args, **kwargs):
        return _FakeResp(self.payload)


class _FakeQuote:
    def __init__(self, best_ask):
        self.best_ask = best_ask


class _FakeDataClient:
    def __init__(self, markets):
        self._markets = markets

    def fetch_active_markets(self, limit):
        return self._markets[:limit]

    def fetch_best_ask(self, token_id):
        return _FakeQuote(0.49)


def test_leader_follow_builds_directional_opportunity():
    market = ParsedMarket(
        market_id="m1",
        question="Match winner?",
        slug="test-market",
        liquidity=10_000.0,
        token_ids=["tok1", "tok2"],
        outcomes=["A", "B"],
        best_asks=[OutcomeQuote(name="A", token_id="tok1", best_ask=0.49)],
        is_sports=True,
    )
    config = _build_config(
        enable_leader_follow=True,
        leader_wallet="0xabc",
        leader_max_signal_age_seconds=500,
        leader_min_notional_usd=50.0,
            leader_price_tolerance_bps=300,
        leader_alpha=0.04,
        max_capital_per_trade=10.0,
        max_bundle_shares=30.0,
        market_cooldown_seconds=0.0,
    )
    strat = LeaderFollowStrategy(config, _FakeDataClient([market]))
    strat.session = _FakeSession(
        [
            {
                "side": "BUY",
                "asset": "tok1",
                "slug": "test-market",
                "outcome": "A",
                "price": 0.48,
                "usdcSize": 120,
                "timestamp": 9_999_999_999,
            }
        ]
    )

    # Make timestamp appear fresh by using wide age bound and a synthetic recent-ish value.
    # current unix in tests is far lower than 9_999_999_999 so we reset with realistic value.
    strat.session = _FakeSession(
        [
            {
                "side": "BUY",
                "asset": "tok1",
                "slug": "test-market",
                "outcome": "A",
                "price": 0.48,
                "usdcSize": 120,
                "timestamp": 1_700_000_000,
            }
        ]
    )
    # still deterministic without monkeypatching time if we allow very large age.
    config = _build_config(
        enable_leader_follow=True,
        leader_wallet="0xabc",
        leader_max_signal_age_seconds=10_000_000_000,
        leader_min_notional_usd=50.0,
            leader_price_tolerance_bps=300,
        leader_alpha=0.04,
        max_capital_per_trade=10.0,
        max_bundle_shares=30.0,
        market_cooldown_seconds=0.0,
    )
    strat = LeaderFollowStrategy(config, _FakeDataClient([market]))
    strat.session = _FakeSession(
        [
            {
                "side": "BUY",
                "asset": "tok1",
                "slug": "test-market",
                "outcome": "A",
                "price": 0.48,
                "usdcSize": 120,
                "timestamp": 1_700_000_000,
            }
        ]
    )
    result = strat.build_opportunities(last_trade_at={})
    opps = result.opportunities
    assert len(opps) == 1
    assert opps[0].group_key.startswith("leader:")
    assert opps[0].expected_profit_usd > 0
    assert result.diagnostics["opportunities_built"] == 1
