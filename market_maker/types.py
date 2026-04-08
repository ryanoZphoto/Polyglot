from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    fetched_at: float

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2.0
        return None

    @property
    def spread(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return ba - bb
        return None


@dataclass(frozen=True)
class ParsedMarket:
    market_id: str
    question: str
    slug: str
    liquidity: float
    active: bool
    closed: bool
    accepting_orders: bool
    enable_orderbook: bool
    neg_risk: bool
    outcomes: list[str]
    token_ids: list[str]
    event_title: str | None
    event_slug: str | None


@dataclass(frozen=True)
class Quote:
    """A two-sided quote the bot intends to post for one token."""
    market_id: str
    token_id: str
    outcome: str
    fair_value: float
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    skew_applied: float
    spread: float


@dataclass(frozen=True)
class InventoryState:
    market_id: str
    token_id: str
    outcome: str
    position_shares: float
    avg_entry_price: float
    unrealized_pnl: float
    side: str  # "long_yes", "long_no", "flat"


@dataclass(frozen=True)
class MMOrder:
    order_id: str
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    status: str  # "pending", "submitted", "filled", "partially_filled", "cancelled", "simulated"
    mode: str  # "dry_run" or "live"
    created_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    order_id: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    fee_usd: float
    rebate_usd: float
    realized_pnl: float
    mode: str
    created_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    markets_quoted: int
    orders_posted: int
    orders_cancelled: int
    fills_detected: int
    total_pnl_this_cycle: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
