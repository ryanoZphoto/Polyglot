"""Type definitions for event trader improved."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BookLevel:
    """Order book price level."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Market order book."""
    bids: list[BookLevel]
    asks: list[BookLevel]
    
    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None
    
    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None
    
    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid
    
    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


@dataclass
class ParsedMarket:
    """Parsed market data."""
    condition_id: str
    token_id: str
    question: str
    outcome: str
    end_date_iso: str | None
    active: bool
    closed: bool
    volume_24h: float
    liquidity: float
    book: OrderBook | None
    
    @staticmethod
    def from_gamma(data: dict[str, Any]) -> ParsedMarket:
        """Parse market from Gamma API response."""
        tokens = data.get("tokens", [])
        token = tokens[0] if tokens else {}
        
        return ParsedMarket(
            condition_id=data.get("condition_id", ""),
            token_id=token.get("token_id", ""),
            question=data.get("question", ""),
            outcome=token.get("outcome", ""),
            end_date_iso=data.get("end_date_iso"),
            active=data.get("active", False),
            closed=data.get("closed", False),
            volume_24h=float(data.get("volume_24hr", 0)),
            liquidity=float(data.get("liquidity", 0)),
            book=None,
        )


@dataclass
class Signal:
    """Trading signal."""
    token_id: str
    question: str
    outcome: str
    market_price: float
    fair_value: float
    edge: float
    size_usd: float
    reason: str
    end_date_iso: str | None
    volume_24h: float
    spread: float
    book_depth: float