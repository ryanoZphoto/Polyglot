from dataclasses import dataclass

from polymarket_bot.execution import LiveExecutor
from polymarket_bot.state import StateStore
from polymarket_bot.types import MarketLeg, Opportunity


@dataclass
class FakeOrderArgs:
    token_id: str
    price: float
    size: float
    side: str


class FakeOrderType:
    GTC = "gtc"


class FakeClient:
    def __init__(self, _host: str, **_kwargs):
        self.submissions = 0
        self.cancelled: list[str] = []

    def create_or_derive_api_creds(self):
        return {"ok": True}

    def set_api_creds(self, _creds):
        return None

    def get_balance_allowance(self):
        return {"balance": "10000"}

    def create_order(self, order_args: FakeOrderArgs):
        return {
            "token_id": order_args.token_id,
            "price": order_args.price,
            "size": order_args.size,
        }

    def post_order(self, _signed, _order_type):
        self.submissions += 1
        if self.submissions == 1:
            return {"orderID": "order-1"}
        raise RuntimeError("post_order failed")

    def cancel(self, order_id: str):
        self.cancelled.append(order_id)


def _opportunity() -> Opportunity:
    legs = [
        MarketLeg(
            market_id="m1",
            market_slug="m1",
            market_question="Q1",
            token_id="t1",
            outcome_name="No",
            price=0.45,
            liquidity=1000,
        ),
        MarketLeg(
            market_id="m2",
            market_slug="m2",
            market_question="Q2",
            token_id="t2",
            outcome_name="No",
            price=0.44,
            liquidity=900,
        ),
    ]
    return Opportunity(
        group_key="event:group",
        legs=legs,
        sum_ask=0.89,
        edge=0.11,
        bundle_shares=10,
        bundle_cost=8.9,
        guaranteed_payout_usd=10,
        expected_profit_usd=1.1,
        min_leg_liquidity=900,
    )


def test_live_executor_cancels_after_partial_failure(tmp_path):
    db = tmp_path / "state.sqlite3"
    state = StateStore(str(db))
    try:
        executor = LiveExecutor(
            clob_host="https://example.com",
            chain_id=137,
            private_key="abc",
            signature_type=0,
            client_class=FakeClient,
            order_args_cls=FakeOrderArgs,
            order_type_cls=FakeOrderType,
            buy_side="BUY",
        )
        result = executor.execute(trade_id="trade-1", opp=_opportunity(), state=state)
        assert result.ok is False
        assert result.errors is not None
        assert "post_order failed" in result.errors[0]
        assert executor.client.cancelled == ["order-1"]
    finally:
        state.close()
