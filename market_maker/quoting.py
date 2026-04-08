from __future__ import annotations

from .config import MMConfig
from .fair_value import book_imbalance, estimate_fair_value
from .inventory import InventoryManager
from .types import OrderBook, Quote


class QuotingEngine:
    def __init__(self, config: MMConfig, inventory_mgr: InventoryManager):
        self.config = config
        self.inventory = inventory_mgr

    def generate_quote(self, market_id: str, token_id: str, outcome: str,
                       book: OrderBook) -> Quote | None:
        """
        Generates a two-sided quote for one token.

        Strategy: undercut the current best bid/ask by 1 tick (0.001) to sit at
        the top of the book. The minimum spread between our bid and ask is
        enforced by config.min_book_spread. Inventory skew shifts both prices
        to encourage unwinding positions.

        Returns None if the book is too thin or doesn't have both sides.
        """
        if not book.bids or not book.asks:
            return None
        if book.best_bid is None or book.best_ask is None:
            return None

        fair = estimate_fair_value(book)
        if fair is None:
            return None

        current_spread = book.spread
        if current_spread is not None and current_spread < self.config.min_book_spread:
            return None

        best_bid = book.best_bid
        best_ask = book.best_ask
        tick = 0.001

        # Place our bid just above the current best bid (undercut the spread)
        # Place our ask just below the current best ask
        bid_price = best_bid + tick
        ask_price = best_ask - tick

        # Ensure our spread is at least min_book_spread
        if ask_price - bid_price < self.config.min_book_spread:
            mid = (best_bid + best_ask) / 2.0
            bid_price = mid - self.config.min_book_spread / 2.0
            ask_price = mid + self.config.min_book_spread / 2.0

        # Apply inventory skew
        pos = self.inventory.get_position(token_id, market_id, outcome)
        skew = self.inventory.compute_skew(pos.position_shares)
        bid_price += skew
        ask_price += skew

        # Apply book imbalance adjustment
        imbalance = book_imbalance(book)
        imbalance_adj = imbalance * self.config.half_spread * 0.2
        bid_price += imbalance_adj
        ask_price += imbalance_adj

        # Round to tick and clamp
        bid_price = max(0.001, min(0.999, round(bid_price, 3)))
        ask_price = max(0.001, min(0.999, round(ask_price, 3)))

        if bid_price >= ask_price:
            return None

        # Reduce size on the overweight side
        bid_size = self.config.quote_size
        ask_size = self.config.quote_size
        if pos.position_shares > 0:
            bid_size = max(1.0, bid_size * (1.0 - abs(pos.position_shares) / max(1.0, self.config.max_inventory_per_market)))
        elif pos.position_shares < 0:
            ask_size = max(1.0, ask_size * (1.0 - abs(pos.position_shares) / max(1.0, self.config.max_inventory_per_market)))

        return Quote(
            market_id=market_id,
            token_id=token_id,
            outcome=outcome,
            fair_value=round(fair, 4),
            bid_price=bid_price,
            bid_size=round(bid_size, 2),
            ask_price=ask_price,
            ask_size=round(ask_size, 2),
            skew_applied=round(skew, 6),
            spread=round(ask_price - bid_price, 4),
        )
