"""
Enhanced position manager with improved exit logic.

Key improvements:
1. Trailing stop activates on ANY profit (not just after TP)
2. Time-decay logic tightens stops near event close
3. Volatility-based trailing stops
"""

import logging
import time
from datetime import datetime, timezone

from event_trader.config import EVConfig
from event_trader.state import EVStateStore
from event_trader.types import OrderBook

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Enhanced position monitoring with better exit triggers.
    
    Improvements:
    - Trailing stop starts at 5% profit (not full TP)
    - Time decay: tighten stops as event approaches
    - Volatility-based stops
    """

    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    def check_exits(
        self,
        books: dict[str, OrderBook],
        api_prices: dict[str, float] | None = None,
    ) -> list[dict]:
        """Check all open positions for exit triggers."""
        exits: list[dict] = []
        positions = self.state.get_open_positions()
        prices = api_prices or {}

        for pos in positions:
            token_id = pos["token_id"]
            book = books.get(token_id)

            # Get current price
            current = prices.get(token_id)
            if current is None and book is not None and book.best_bid is not None:
                current = book.best_bid
            if current is None:
                continue

            # Calculate P&L
            entry = pos["entry_price"]
            size = pos["size"]
            pnl = (current - entry) * size
            pnl_pct = (current - entry) / entry if entry > 0 else 0

            # Get tier-specific targets
            tier = pos.get("tier", "mid")
            tp_pct, sl_pct = self._get_tier_targets(tier)

            # NEW: Check time decay
            close_time = pos.get("close_time")
            if close_time:
                sl_pct = self._apply_time_decay(sl_pct, close_time)

            # Check take profit
            if pnl_pct >= tp_pct:
                exits.append({
                    "position_id": pos["position_id"],
                    "token_id": token_id,
                    "exit_price": current,
                    "reason": "take_profit",
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                })
                continue

            # Check stop loss
            if pnl_pct <= -sl_pct:
                exits.append({
                    "position_id": pos["position_id"],
                    "token_id": token_id,
                    "exit_price": current,
                    "reason": "stop_loss",
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                })
                continue

            # NEW: Improved trailing stop logic
            # Activates at trailing_activation_pct (default 5%) instead of full TP
            if pnl_pct >= self.config.trailing_activation_pct:
                trailing_triggered = self._check_trailing_stop(
                    pos, current, pnl_pct, tier
                )
                if trailing_triggered:
                    exits.append({
                        "position_id": pos["position_id"],
                        "token_id": token_id,
                        "exit_price": current,
                        "reason": "trailing_stop",
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                    })

        return exits

    def _get_tier_targets(self, tier: str) -> tuple[float, float]:
        """Get take-profit and stop-loss for tier."""
        if tier == "longshot":
            return (
                self.config.longshot_take_profit_pct,
                self.config.longshot_stop_loss_pct,
            )
        elif tier == "highprob":
            return (
                self.config.highprob_take_profit_pct,
                self.config.highprob_stop_loss_pct,
            )
        else:
            return (
                self.config.take_profit_pct,
                self.config.stop_loss_pct,
            )

    def _apply_time_decay(self, stop_loss_pct: float, close_time: int) -> float:
        """
        Tighten stop loss as event approaches close.
        
        Within time_decay_days of close, use tighter stop.
        """
        if close_time <= 0:
            return stop_loss_pct

        now = int(time.time())
        seconds_to_close = close_time - now
        days_to_close = seconds_to_close / 86400

        if days_to_close <= self.config.time_decay_days:
            # Use tighter stop near close
            tighter_stop = self.config.time_decay_stop_pct
            logger.debug(
                "Time decay: %.1f days to close, tightening stop %.1%% -> %.1%%",
                days_to_close, stop_loss_pct * 100, tighter_stop * 100
            )
            return max(tighter_stop, stop_loss_pct)  # Don't loosen

        return stop_loss_pct

    def _check_trailing_stop(
        self,
        pos: dict,
        current_price: float,
        current_pnl_pct: float,
        tier: str,
    ) -> bool:
        """
        Enhanced trailing stop logic.
        
        Activates on ANY profit >= trailing_activation_pct.
        Uses volatility-based cushion.
        """
        # Get high-water mark
        high_water = pos.get("high_water_price", pos["entry_price"])
        
        # Update high-water if current is higher
        if current_price > high_water:
            self.state.update_position_high_water(pos["position_id"], current_price)
            high_water = current_price

        # Calculate trailing stop price
        # Stop = high_water * (1 - trailing_pct)
        # But use volatility multiplier for better adaptation
        
        # Estimate volatility from price movement
        entry = pos["entry_price"]
        price_range = high_water - entry
        estimated_vol = price_range / entry if entry > 0 else 0.10

        # Trailing cushion = max(config trailing %, volatility * multiplier)
        trailing_pct = max(
            self.config.trailing_stop_pct,
            estimated_vol * self.config.volatility_multiplier
        )

        stop_price = high_water * (1 - trailing_pct)

        # Trigger if current drops below stop
        if current_price <= stop_price:
            logger.info(
                "Trailing stop triggered: current=%.3f <= stop=%.3f (high=%.3f, trail=%.1%%)",
                current_price, stop_price, high_water, trailing_pct * 100
            )
            return True

        return False