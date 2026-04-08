from __future__ import annotations

from .types import OrderBook


def estimate_fair_value(book: OrderBook, depth_levels: int = 5) -> float | None:
    """
    Depth-weighted mid-price.

    Uses the top N levels on each side, weighting each level's price by its
    size. This pulls the estimate toward the side with more resting liquidity,
    which is a better proxy for fair value than a naive mid.
    """
    if not book.bids or not book.asks:
        return book.mid

    bid_levels = book.bids[:depth_levels]
    ask_levels = book.asks[:depth_levels]

    bid_value = sum(lvl.price * lvl.size for lvl in bid_levels)
    bid_weight = sum(lvl.size for lvl in bid_levels)
    ask_value = sum(lvl.price * lvl.size for lvl in ask_levels)
    ask_weight = sum(lvl.size for lvl in ask_levels)

    total_weight = bid_weight + ask_weight
    if total_weight <= 0:
        return book.mid

    fair = (bid_value + ask_value) / total_weight

    # Clamp to within the best bid/ask to avoid quoting outside the book
    best_bid = book.bids[0].price
    best_ask = book.asks[0].price
    return max(best_bid, min(best_ask, fair))


def book_imbalance(book: OrderBook, depth_levels: int = 5) -> float:
    """
    Returns a value in [-1, 1].
    Positive = more bid-side weight (price likely to move up).
    Negative = more ask-side weight (price likely to move down).
    """
    if not book.bids or not book.asks:
        return 0.0

    bid_weight = sum(lvl.size for lvl in book.bids[:depth_levels])
    ask_weight = sum(lvl.size for lvl in book.asks[:depth_levels])
    total = bid_weight + ask_weight
    if total <= 0:
        return 0.0
    return (bid_weight - ask_weight) / total
