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

    @property
    def bid_depth(self) -> float:
        return sum(b.size for b in self.bids)

    @property
    def ask_depth(self) -> float:
        return sum(a.size for a in self.asks)


@dataclass(frozen=True)
class ParsedMarket:
    market_id: str
    question: str
    slug: str
    liquidity: float
    volume: float
    active: bool
    closed: bool
    accepting_orders: bool
    enable_orderbook: bool
    neg_risk: bool
    outcomes: list[str]
    token_ids: list[str]
    outcome_prices: list[float]
    event_title: str | None
    event_slug: str | None
    end_date: str | None
    description: str | None


@dataclass(frozen=True)
class Signal:
    """A detected opportunity -- a contract the bot considers buying."""
    signal_id: str
    market_id: str
    token_id: str
    outcome: str
    question: str
    current_price: float
    entry_price: float
    target_price: float
    stop_loss_price: float
    confidence: float
    reason: str
    book_bid: float
    book_ask: float
    book_spread: float
    bid_depth: float
    ask_depth: float
    liquidity: float
    volume: float
    tier: str = "mid"  # "longshot", "mid", or "highprob"
    sized_usd: float = 5.0  # per-signal position size in USD
    created_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class Position:
    """An active holding of contracts."""
    position_id: str
    market_id: str
    token_id: str
    outcome: str
    question: str
    side: str  # "BUY" -- we buy contracts
    entry_price: float
    current_price: float
    contracts: float
    cost_basis_usd: float
    current_value_usd: float
    unrealized_pnl_usd: float
    pnl_pct: float
    target_price: float
    stop_loss_price: float
    status: str  # "open", "closing", "closed"
    mode: str  # "dry_run" or "live"
    entry_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class TradeOrder:
    order_id: str
    position_id: str
    market_id: str
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float
    status: str  # "submitted", "filled", "cancelled", "simulated"
    mode: str
    reason: str  # "entry", "take_profit", "stop_loss", "manual_exit"
    created_at: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    order_id: str
    position_id: str
    market_id: str
    token_id: str
    side: str
    price: float
    size: float
    notional_usd: float
    fee_usd: float
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
    cycle_number: int
    markets_scanned: int
    signals_found: int
    entries_placed: int
    exits_placed: int
    fills_detected: int
    positions_open: int
    total_unrealized_pnl: float
    total_realized_pnl: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
