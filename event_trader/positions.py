from __future__ import annotations

import logging

from .config import EVConfig
from .state import EVStateStore
from .types import OrderBook

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Monitors open positions and decides when to exit.

    Uses API mid-market prices (outcomePrices) for valuation, not raw
    book bids.  On Polymarket the CLOB best_bid for a $0.02 contract is
    usually $0.001 -- that's not the real price you could sell at.

    Exit triggers (compared against API price):
    - Take profit: api_price >= stored target_price
    - Stop loss:   api_price <= stored stop_loss_price
    - Trailing stop: api_price drops trailing_stop_pct from high-water
    """

    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    def check_exits(
        self,
        books: dict[str, OrderBook],
        api_prices: dict[str, float] | None = None,
    ) -> list[dict]:
        """
        Check all open positions against current prices.

        api_prices maps token_id -> outcomePrices mid-market value.
        Falls back to book.best_bid when api_prices is unavailable.
        """
        exits: list[dict] = []
        positions = self.state.get_open_positions()
        prices = api_prices or {}

        for pos in positions:
            token_id = pos["token_id"]
            book = books.get(token_id)

            current = prices.get(token_id)
            if current is None and book is not None and book.best_bid is not None:
                current = book.best_bid
            if current is None:
                continue

            self.state.update_position_price(pos["position_id"], current)

            entry_price = float(pos["entry_price"])
            contracts = float(pos["contracts"])
            cost_basis = float(pos["cost_basis_usd"])
            high_water = float(pos["high_water_price"])

            current_value = current * contracts
            unrealized_pnl = current_value - cost_basis
            pnl_pct = (current - entry_price) / entry_price if entry_price > 0 else 0

            exit_reason = None

            target = float(pos["target_price"])
            stop = float(pos["stop_loss_price"])

            if current >= target:
                exit_reason = "take_profit"
            elif current <= stop:
                exit_reason = "stop_loss"
            elif self.config.trailing_stop_pct > 0 and high_water > entry_price:
                trailing_threshold = high_water * (1.0 - self.config.trailing_stop_pct)
                if current <= trailing_threshold:
                    exit_reason = "trailing_stop"

            if exit_reason is not None:
                exits.append({
                    "position_id": pos["position_id"],
                    "market_id": pos["market_id"],
                    "token_id": token_id,
                    "outcome": pos["outcome"],
                    "question": pos["question"],
                    "entry_price": entry_price,
                    "current_price": current,
                    "contracts": contracts,
                    "cost_basis": cost_basis,
                    "current_value": round(current_value, 4),
                    "unrealized_pnl": round(unrealized_pnl, 4),
                    "pnl_pct": round(pnl_pct, 4),
                    "high_water": high_water,
                    "reason": exit_reason,
                    "book": book,
                })

                logger.info(
                    "EXIT_SIGNAL: %s  %s  entry=%.3f now=%.3f pnl=%.2f%% ($%.4f)  %s",
                    exit_reason, token_id[:20],
                    entry_price, current,
                    pnl_pct * 100, unrealized_pnl,
                    pos["question"][:50],
                )

        return exits

    def get_portfolio_summary(
        self,
        books: dict[str, OrderBook],
        api_prices: dict[str, float] | None = None,
    ) -> dict:
        """Build a snapshot of the entire portfolio for logging."""
        positions = self.state.get_open_positions()
        prices = api_prices or {}
        total_cost = 0.0
        total_value = 0.0
        total_pnl = 0.0
        details = []

        for pos in positions:
            token_id = pos["token_id"]
            entry = float(pos["entry_price"])
            contracts = float(pos["contracts"])
            cost = float(pos["cost_basis_usd"])

            book = books.get(token_id)
            current = prices.get(token_id)
            if current is None and book and book.best_bid:
                current = book.best_bid
            if current is None:
                current = float(pos["current_price"])

            value = current * contracts
            pnl = value - cost

            total_cost += cost
            total_value += value
            total_pnl += pnl

            details.append({
                "token": token_id[:20],
                "outcome": pos["outcome"],
                "entry": entry,
                "current": round(current, 3),
                "contracts": contracts,
                "cost": round(cost, 2),
                "value": round(value, 2),
                "pnl": round(pnl, 4),
                "pnl_pct": round((current - entry) / entry * 100, 1) if entry > 0 else 0,
            })

        return {
            "open_positions": len(positions),
            "total_cost_usd": round(total_cost, 2),
            "total_value_usd": round(total_value, 2),
            "total_unrealized_pnl": round(total_pnl, 4),
            "positions": details,
        }
