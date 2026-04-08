"""
Enhanced scanner with edge calculation and market quality filters.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from event_trader_improved.config import EVConfig
from event_trader.data_client import EVDataClient
from event_trader.state import EVStateStore
from event_trader.types import OrderBook, ParsedMarket, Signal

logger = logging.getLogger(__name__)


class EVScanner:
    def __init__(self, config: EVConfig, client: EVDataClient, state: EVStateStore):
        self.config = config
        self.client = client
        self.state = state

    def scan(self, markets: list[ParsedMarket]) -> list[Signal]:
        """
        Scan markets and generate signals with edge calculation.
        
        Fetches order books and evaluates each token (outcome).
        """
        signals = []
        
        # Filter markets first
        candidates = []
        for mkt in markets:
            if not mkt.active or mkt.closed or not mkt.accepting_orders:
                continue
            if not mkt.enable_orderbook:
                continue
            if mkt.liquidity < self.config.min_liquidity:
                continue
            if mkt.volume < self.config.min_volume:
                continue
            candidates.append(mkt)
        
        logger.info(f"filtered to {len(candidates)} candidate markets from {len(markets)}")
        
        # Fetch order books in parallel
        books_by_token = {}
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {}
            for mkt in candidates:
                for token_id in mkt.token_ids:
                    fut = executor.submit(self.client.fetch_order_book, token_id)
                    futures[fut] = (mkt, token_id)
            
            for fut in as_completed(futures):
                mkt, token_id = futures[fut]
                try:
                    book = fut.result()
                    if book:
                        books_by_token[token_id] = (mkt, book)
                except Exception as e:
                    logger.warning(f"failed to fetch book for {token_id}: {e}")
        
        logger.info(f"fetched {len(books_by_token)} order books")
        
        # Evaluate each token
        for token_id, (mkt, book) in books_by_token.items():
            # Skip if already have position
            if self.state.has_position_for_token(token_id):
                continue
            
            # Get best ask price
            if not book.asks or len(book.asks) == 0:
                continue
            
            best_ask = float(book.asks[0]["price"])
            ask_size = float(book.asks[0]["size"])
            
            # Price filters
            if best_ask > self.config.max_entry_price:
                continue
            if best_ask < self.config.min_entry_price:
                continue
            
            # Market quality filters
            if not self._check_market_quality(book, best_ask):
                continue
            
            # Calculate edge
            edge = self._calculate_edge(best_ask)
            if edge < self.config.min_edge:
                continue
            
            # Classify tier
            tier = self._classify_tier(best_ask)
            
            # Calculate position size
            size_usd = self._calculate_position_size(best_ask, edge, tier)
            shares = min(
                size_usd / best_ask,
                self.config.max_contracts_per_entry,
                ask_size * 0.8  # Don't take full book depth
            )
            
            # Get outcome name
            idx = mkt.token_ids.index(token_id)
            outcome = mkt.outcomes[idx] if idx < len(mkt.outcomes) else f"Outcome {idx}"
            
            # Create signal
            signal = Signal(
                token_id=token_id,
                condition_id=mkt.condition_id,
                market_slug=mkt.slug,
                question=mkt.question,
                outcome=outcome,
                side="BUY",
                price=best_ask,
                size=shares,
                tier=tier,
                edge=edge,
                confidence=self._calculate_confidence(edge, book, best_ask),
            )
            signals.append(signal)
        
        logger.info(f"generated {len(signals)} signals with min_edge={self.config.min_edge}")
        return signals

    def _check_market_quality(self, book: OrderBook, price: float) -> bool:
        """Check spread, depth, and volume quality."""
        if not book.bids or not book.asks:
            return False
        
        best_bid = float(book.bids[0]["price"])
        best_ask = float(book.asks[0]["price"])
        
        # Check spread
        spread = best_ask - best_bid
        if spread / price > self.config.max_spread_pct:
            return False
        
        # Check book depth
        ask_depth = sum(float(level["size"]) * float(level["price"]) for level in book.asks[:3])
        if ask_depth < self.config.min_book_depth_usd:
            return False
        
        return True

    def _calculate_edge(self, price: float) -> float:
        """
        Calculate edge (expected value).
        
        Simple heuristic: assume market is slightly inefficient.
        In production, use your own probability model.
        """
        # Longshots tend to be overpriced
        if price < 0.20:
            return 0.12  # 12% edge
        elif price < 0.30:
            return 0.08  # 8% edge
        # Mid-range
        elif price < 0.50:
            return 0.06  # 6% edge
        # Favorites tend to be underpriced
        elif price < 0.70:
            return 0.07  # 7% edge
        else:
            return 0.05  # 5% edge

    def _calculate_confidence(self, edge: float, book: OrderBook, price: float) -> float:
        """Calculate confidence score (0-1) based on edge and market quality."""
        # Base confidence from edge
        conf = min(edge / 0.15, 1.0)  # 15% edge = max confidence
        
        # Adjust for book depth
        if book.asks:
            depth = sum(float(level["size"]) for level in book.asks[:3])
            if depth > 1000:
                conf *= 1.1
            elif depth < 100:
                conf *= 0.9
        
        return min(conf, 1.0)

    def _classify_tier(self, price: float) -> Literal["LONGSHOT", "MID", "HIGHPROB"]:
        """Classify position tier based on price."""
        if price <= self.config.longshot_ceiling:
            return "LONGSHOT"
        elif price >= self.config.highprob_floor:
            return "HIGHPROB"
        else:
            return "MID"

    def _calculate_position_size(self, price: float, edge: float, 
                                  tier: Literal["LONGSHOT", "MID", "HIGHPROB"]) -> float:
        """
        Calculate position size using Kelly Criterion or fixed sizing.
        """
        if not self.config.use_kelly_sizing:
            # Fixed sizing by tier
            if tier == "LONGSHOT":
                return self.config.longshot_size_usd
            elif tier == "HIGHPROB":
                return self.config.highprob_size_usd
            else:
                return self.config.position_size_usd
        
        # Kelly sizing: f = edge / odds
        # For binary outcome: f = (p * (b+1) - 1) / b
        # where p = true probability, b = odds
        # Simplified: f ≈ edge / price
        bankroll = self.config.max_total_exposure_usd
        kelly_fraction = edge / price
        kelly_size = self.config.kelly_fraction * kelly_fraction * bankroll
        
        # Cap at max position percentage
        max_size = self.config.max_position_pct * bankroll
        
        # Tier-based minimum
        if tier == "LONGSHOT":
            min_size = self.config.longshot_size_usd
        elif tier == "HIGHPROB":
            min_size = self.config.highprob_size_usd
        else:
            min_size = self.config.position_size_usd
        
        return max(min_size, min(kelly_size, max_size))





