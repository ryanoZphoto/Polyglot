from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import EVConfig
from .data_client import EVDataClient
from .state import EVStateStore
from .types import OrderBook, ParsedMarket, Signal

logger = logging.getLogger(__name__)


class OpportunityScanner:
    """
    Scans all active markets for cheap contracts worth buying.

    A good entry has:
    - Low current price (cheap contracts with room to move up)
    - Sufficient book depth (can actually buy without huge slippage)
    - Reasonable spread (not an illiquid trap)
    - Price momentum or recent volume suggesting activity
    - Not already held in an open position
    """

    def __init__(self, config: EVConfig, client: EVDataClient, state: EVStateStore):
        self.config = config
        self.client = client
        self.state = state

    def scan(self, markets: list[ParsedMarket]) -> list[Signal]:
        signals: list[Signal] = []
        skip_reasons: dict[str, int] = {
            "inactive": 0, "no_orderbook": 0, "bad_outcomes": 0,
            "low_liquidity": 0, "no_book": 0, "too_expensive": 0,
            "too_cheap": 0, "wide_spread": 0, "no_depth": 0,
            "has_position": 0, "recent_signal": 0, "low_confidence": 0,
            "no_asks": 0, "price_prefilter": 0,
        }

        # Phase 1: fast pre-filter using outcome_prices from the API
        # Only fetch order books for tokens that look cheap enough
        candidates: list[tuple[ParsedMarket, int, str]] = []
        for m in markets:
            if not m.active or m.closed or not m.accepting_orders:
                skip_reasons["inactive"] += 1
                continue
            if not m.enable_orderbook:
                skip_reasons["no_orderbook"] += 1
                continue
            if len(m.outcomes) < 2 or len(m.token_ids) < 2:
                skip_reasons["bad_outcomes"] += 1
                continue
            if m.liquidity < self.config.min_liquidity:
                skip_reasons["low_liquidity"] += 1
                continue

            for i, tid in enumerate(m.token_ids):
                api_price = m.outcome_prices[i] if i < len(m.outcome_prices) else 0.0
                if api_price > self.config.max_entry_price:
                    skip_reasons["price_prefilter"] += 1
                    continue
                if api_price < self.config.min_entry_price and api_price > 0:
                    skip_reasons["price_prefilter"] += 1
                    continue
                candidates.append((m, i, tid))

        logger.info(
            "pre-filter: %d candidates from %d markets (skipped %d expensive)",
            len(candidates), len(markets), skip_reasons["price_prefilter"],
        )

        # Phase 2: fetch books only for cheap candidates
        if candidates:
            books = self._fetch_books([t[2] for t in candidates])
        else:
            books = {}

        for m, idx, token_id in candidates:
            book = books.get(token_id)
            if book is None:
                skip_reasons["no_book"] += 1
                continue

            sig = self._evaluate_token(m, idx, token_id, book, skip_reasons)
            if sig is not None:
                signals.append(sig)

        signals.sort(key=lambda s: s.confidence, reverse=True)

        logger.info(
            "scan summary: %d candidates checked, %d signals, filters: %s",
            len(candidates), len(signals), skip_reasons,
        )

        return signals

    def _fetch_books(self, token_ids: list[str]) -> dict[str, OrderBook]:
        books: dict[str, OrderBook] = {}
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futures = {
                pool.submit(self.client.fetch_order_book, tid): tid
                for tid in token_ids
            }
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    books[tid] = future.result()
                except Exception:
                    logger.debug("book fetch failed for %s", tid[:16])
        return books

    def _evaluate_token(self, market: ParsedMarket, idx: int,
                        token_id: str, book: OrderBook,
                        skip_reasons: dict[str, int] | None = None) -> Signal | None:
        """
        Evaluate using the API's outcome price as the reference entry price.

        On Polymarket, the order book for most markets has bids at ~$0.01 and
        asks at ~$0.99. The actual market price is reflected in the API's
        outcomePrices field. A GTC limit order at the API price will sit in
        the book and get filled when the market crosses that level.
        """
        sr = skip_reasons or {}

        if self.state.has_position_for_token(token_id):
            sr["has_position"] = sr.get("has_position", 0) + 1
            return None

        if self.state.was_signal_recently_seen(token_id, minutes=10):
            sr["recent_signal"] = sr.get("recent_signal", 0) + 1
            return None

        api_price = market.outcome_prices[idx] if idx < len(market.outcome_prices) else 0.0
        if api_price <= 0:
            sr["too_cheap"] = sr.get("too_cheap", 0) + 1
            return None

        entry_price = api_price

        if entry_price > self.config.max_entry_price:
            sr["too_expensive"] = sr.get("too_expensive", 0) + 1
            return None
        if entry_price < self.config.min_entry_price:
            sr["too_cheap"] = sr.get("too_cheap", 0) + 1
            return None

        best_bid = book.best_bid or 0
        best_ask = book.best_ask or 1.0

        ask_depth_usd = sum(a.price * a.size for a in book.asks[:10])
        if ask_depth_usd < self.config.min_book_depth_usd:
            sr["no_depth"] = sr.get("no_depth", 0) + 1
            return None

        confidence = self._score_opportunity(market, book, entry_price)
        if confidence < 0.1:
            sr["low_confidence"] = sr.get("low_confidence", 0) + 1
            return None

        tier, sized_usd, tp_pct, sl_pct = self._classify_tier(entry_price)
        target_price = min(0.95, entry_price * (1.0 + tp_pct))
        stop_loss_price = max(0.001, entry_price * (1.0 - sl_pct))

        outcome = market.outcomes[idx] if idx < len(market.outcomes) else "?"
        spread = book.spread or 0

        reason = self._build_reason(market, book, entry_price, confidence)

        return Signal(
            signal_id=f"sig_{uuid.uuid4().hex[:12]}",
            market_id=market.market_id,
            token_id=token_id,
            outcome=outcome,
            question=market.question[:120],
            current_price=entry_price,
            entry_price=entry_price,
            target_price=round(target_price, 3),
            stop_loss_price=round(stop_loss_price, 3),
            confidence=round(confidence, 3),
            reason=reason,
            book_bid=best_bid,
            book_ask=best_ask,
            book_spread=spread,
            bid_depth=book.bid_depth,
            ask_depth=book.ask_depth,
            liquidity=market.liquidity,
            volume=market.volume,
            tier=tier,
            sized_usd=sized_usd,
        )

    def _score_opportunity(self, market: ParsedMarket, book: OrderBook,
                           entry_price: float) -> float:
        """
        Score from 0 to 1. Higher = more attractive entry.

        On Polymarket, all books have wide spreads (bid ~0.01, ask ~0.99).
        Scoring is based on the API price, market liquidity, and volume.
        """
        score = 0.0

        # Price score: cheaper contracts have more upside room
        price_score = max(0, 1.0 - (entry_price / self.config.max_entry_price))
        score += price_score * 0.40

        # Liquidity score: more liquid = easier to exit when price moves
        liq_score = min(1.0, market.liquidity / 50000)
        score += liq_score * 0.25

        # Volume score: active markets move more, creating opportunities
        vol_score = min(1.0, market.volume / 100000)
        score += vol_score * 0.25

        # Book depth: at least some depth means orders will fill
        total_depth = book.bid_depth + book.ask_depth
        depth_score = min(1.0, total_depth / 10000)
        score += depth_score * 0.10

        return min(1.0, score)

    def _classify_tier(self, price: float) -> tuple[str, float, float, float]:
        """Return (tier_name, position_size_usd, take_profit_pct, stop_loss_pct)."""
        if price <= self.config.longshot_ceiling:
            return (
                "longshot",
                self.config.longshot_size_usd,
                self.config.longshot_take_profit_pct,
                self.config.longshot_stop_loss_pct,
            )
        if price >= self.config.highprob_floor:
            return (
                "highprob",
                self.config.highprob_size_usd,
                self.config.highprob_take_profit_pct,
                self.config.highprob_stop_loss_pct,
            )
        return (
            "mid",
            self.config.position_size_usd,
            self.config.take_profit_pct,
            self.config.stop_loss_pct,
        )

    def _build_reason(self, market: ParsedMarket, book: OrderBook,
                      price: float, confidence: float) -> str:
        tier, sized_usd, _, _ = self._classify_tier(price)
        parts = [f"price=${price:.3f}", f"tier={tier}", f"size=${sized_usd:.0f}"]
        parts.append(f"liq=${market.liquidity:.0f}")
        parts.append(f"vol=${market.volume:.0f}")
        if book.spread is not None:
            parts.append(f"spread={book.spread:.3f}")
        ask_depth = sum(a.price * a.size for a in book.asks[:5])
        parts.append(f"depth=${ask_depth:.0f}")
        parts.append(f"conf={confidence:.2f}")
        return " | ".join(parts)
