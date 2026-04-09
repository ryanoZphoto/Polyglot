"""Improved position management with profit locking and aggressive trailing stops."""

import logging
from datetime import datetime, timezone
from .config import ImprovedEVConfig

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(self, config: ImprovedEVConfig, bankroll_usd: float):
        self.config = config
        self.bankroll_usd = bankroll_usd
    
    def calculate_size(self, signal) -> float:
        """Calculate position size using Kelly or fixed sizing."""
        if self.config.use_kelly_sizing:
            # Kelly: f* = (bp - q) / b
            # Simplified: fraction = edge * kelly_fraction
            kelly_fraction = signal.edge * self.config.kelly_fraction
            size = self.bankroll_usd * kelly_fraction
        else:
            size = self.config.max_position_size_usd
        
        # Risk caps
        return min(size, self.config.max_position_size_usd)
    
    def check_exits(self, positions: list, live_prices: dict) -> list[dict]:
        """
        Evaluate all open positions for potential exits.
        Returns a list of positions that should be closed.
        """
        exit_signals = []
        
        for pos in positions:
            token_id = pos["token_id"]
            current_price = live_prices.get(token_id)
            
            if current_price is None:
                continue
                
            entry_price = float(pos["entry_price"])
            high_water = float(pos["high_water_price"] if "high_water_price" in pos.keys() else entry_price)
            
            # 1. Update High Water Mark
            if current_price > high_water:
                high_water = current_price
            
            pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            exit_reason = None
            
            # --- THE GENIUS EXIT LOGIC (Primal Sweet Spot) ---
            
            # 1. Take Profit (Hard Target)
            target = float(pos["target_price"] if "target_price" in pos.keys() else entry_price * 1.2)
            if current_price >= target:
                exit_reason = "TAKE_PROFIT"
            
            # 2. Stop Loss (Hard Floor)
            stop = float(pos["stop_loss_price"] if "stop_loss_price" in pos.keys() else entry_price * 0.8)
            if current_price <= stop:
                exit_reason = "STOP_LOSS"
                
            # 3. BREAK-EVEN PROTECTION (The "Profit Locker")
            # If we are up by more than aggressive_trailing_activation (e.g. 2%), 
            # we move the floor to break-even + buffer.
            if pnl_pct >= self.config.aggressive_trailing_activation:
                break_even_floor = entry_price + self.config.break_even_buffer
                if current_price <= break_even_floor:
                    exit_reason = "PROFIT_LOCK_BREAK_EVEN"
            
            # 4. AGGRESSIVE TRAILING (Locking in bigger moves)
            # If we were up significantly, don't give back more than 50% of the gains
            gain = high_water - entry_price
            if gain > (entry_price * 0.05): # If we were up > 5%
                trailing_floor = high_water - (gain * 0.3) # Give back only 30% of max gain
                if current_price <= trailing_floor:
                    exit_reason = "AGGRESSIVE_TRAILING"

            if exit_reason:
                exit_signals.append({
                    "pos": pos,
                    "reason": exit_reason,
                    "price": current_price,
                    "pnl_pct": pnl_pct,
                    "high_water": high_water
                })
        
        return exit_signals


