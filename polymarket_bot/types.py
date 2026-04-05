from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class OutcomeQuote:
    name: str
    token_id: str
    best_ask: float | None


@dataclass
class ParsedMarket:
    market_id: str
    question: str
    slug: str
    liquidity: float
    token_ids: List[str]
    outcomes: List[str]
    best_asks: List[OutcomeQuote]
    event_title: str | None = None
    event_slug: str | None = None
    group_item_title: str | None = None
    active: bool = True
    closed: bool = False
    accepting_orders: bool = True
    enable_orderbook: bool = True
    neg_risk: bool = False
    is_sports: bool = False

    @property
    def id(self) -> str:
        return self.market_id


@dataclass(frozen=True)
class Opportunity:
    group_key: str
    legs: List["MarketLeg"]
    sum_ask: float
    edge: float
    bundle_shares: float
    bundle_cost: float
    guaranteed_payout_usd: float
    expected_profit_usd: float
    min_leg_liquidity: float

    @property
    def sum_best_asks(self) -> float:
        return self.sum_ask

    @property
    def total_cost_usd(self) -> float:
        return self.bundle_cost

    @property
    def guaranteed_profit_usd(self) -> float:
        return self.expected_profit_usd

    @property
    def market_id(self) -> str:
        # Cooldown/accounting key used by existing bot flow.
        return self.group_key


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    mode: str
    message: str


@dataclass
class MarketLeg:
    market_id: str
    market_slug: str
    market_question: str
    token_id: str
    outcome_name: str
    price: float
    liquidity: float
