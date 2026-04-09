"""Runtime orchestration for improved event trader."""

import logging
import time

from event_trader.data_client import EVDataClient

from .config import ImprovedEVConfig
from .scanner import Scanner
from .positions import PositionManager
from .state_wrapper import ImprovedStateWrapper

logger = logging.getLogger(__name__)


def run_once(
    config: ImprovedEVConfig,
    client: EVDataClient,
    scanner: Scanner,
    pos_mgr: PositionManager,
    state: ImprovedStateWrapper,
    cycle_number: int,
) -> dict:
    """Run one complete cycle: update prices, check exits, scan for entries."""
    
    cycle_id = f"ev_{time.strftime('%Y%m%dT%H%M%S')}_{hash(time.time()) & 0xffffff:06x}"
    logger.info("=== Cycle %s (#%d) ===", cycle_id, cycle_number)
    
    if state.is_paused():
        logger.info("Bot is PAUSED - skipping cycle")
        return {"cycle_id": cycle_id, "paused": True}
    
    # 1. FETCH PRICES & MARKETS
    try:
        all_markets = client.fetch_active_markets()
        
        # Identify tokens we need prices for (all active markets + open positions)
        open_positions = state.get_open_positions()
        token_ids_to_fetch = {p["token_id"] for p in open_positions}
        
        # Build initial price lookup from market scan
        live_prices = {}
        for m in all_markets:
            if hasattr(m, "token_ids"):
                for tid, price in zip(m.token_ids, m.outcome_prices):
                    live_prices[tid] = price
        
        # Ensure we have prices for all open positions (even if not in top 600)
        missing_tids = [tid for tid in token_ids_to_fetch if tid not in live_prices]
        if missing_tids:
            logger.info("Fetching specific prices for %d missing tokens...", len(missing_tids))
            pos_prices = client.fetch_live_prices(missing_tids)
            live_prices.update(pos_prices)
            
    except Exception as e:
        logger.error("Failed to fetch markets/prices: %s", e)
        return {"cycle_id": cycle_id, "error": str(e)}

    # 2. MANAGE EXISTING POSITIONS (The "Profit Machine")
    exits_executed = 0
    if open_positions:
        logger.info("Checking %d open positions for exits...", len(open_positions))
        # Update current prices in DB
        for p in open_positions:
            current = live_prices.get(p["token_id"])
            if current is not None:
                state.update_position_price(p["position_id"], current)
        
        # Re-fetch with updated prices to check logic
        updated_positions = state.get_open_positions()
        exit_recommendations = pos_mgr.check_exits(updated_positions, live_prices)
        
        for exit in exit_recommendations:
            pos = exit["pos"]
            logger.info(
                "DRY_SELL: [%s] %s @ $%.3f (PnL: %.2f%%) Reason: %s",
                pos["outcome"], pos["question"][:50], exit["price"], 
                exit["pnl_pct"] * 100, exit["reason"]
            )
            state.close_position(pos["position_id"], exit["price"], exit["reason"])
            exits_executed += 1

    # 3. SCAN FOR NEW ENTRIES
    try:
        signals = scanner.scan(all_markets)
        # Filter signals for tokens we already own
        owned_tokens = {p["token_id"] for p in state.get_open_positions()}
        signals = [s for s in signals if s.token_id not in owned_tokens]
        
        for i, sig in enumerate(signals[:3], 1):
            logger.info(
                "Signal #%d: [%s] %s @ $%.3f (edge=%.1f%%)",
                i, sig.strategy, sig.outcome[:40], sig.price, sig.edge * 100
            )
    except Exception as e:
        logger.error("Scanner failed: %s", e, exc_info=True)
        return {"cycle_id": cycle_id, "error": str(e)}
    
    # 4. EXECUTE ENTRIES (Dry Run)
    executed = 0
    # Apply risk limits
    current_positions = state.get_open_positions()
    current_count = len(current_positions)
    current_invested = sum(p["cost_basis_usd"] for p in current_positions)
    
    space_available = config.max_positions - current_count
    budget_remaining = config.max_total_exposure_usd - current_invested
    
    if budget_remaining <= 0:
        logger.info("BUDGET_CAP: Skipping entries (invested=$%.2f, limit=$%.2f)", current_invested, config.max_total_exposure_usd)
        space_available = 0

    for signal in signals[:max(0, space_available)]:
        if budget_remaining <= 0:
            break
            
        size_usd = pos_mgr.calculate_size(signal)
        # Ensure we don't exceed remaining budget
        size_usd = min(size_usd, budget_remaining)
        
        if size_usd < 1.0: # Don't place dust trades
            continue
            
        signal.size_usd = size_usd
        logger.info(
            "DRY_BUY: [%s] Would buy %s @ $%.3f for $%.2f (edge=%.1f%%)",
            signal.strategy, signal.outcome[:40], signal.price, signal.size_usd, signal.edge * 100
        )
        state.record_dry_run_signal(cycle_id, signal)
        state.record_dry_run_position(signal)
        
        executed += 1
        budget_remaining -= size_usd
        if budget_remaining <= 0:
            logger.info("BUDGET_HIT: Stopping entries for this cycle")
            break
    
    state.record_cycle(
        cycle_id=cycle_id,
        cycle_number=cycle_number,
        scanned_markets=len(all_markets),
        signals_found=len(signals),
        entries_placed=executed,
        exits_placed=exits_executed
    )
    
    return {
        "cycle_id": cycle_id,
        "markets_scanned": len(all_markets),
        "signals_found": len(signals),
        "executed": executed,
        "exits": exits_executed
    }


def run_loop(
    config: ImprovedEVConfig,
    db_path_override: str | None = None,
) -> None:
    """Main event loop."""
    
    client = EVDataClient(config)
    
    state = ImprovedStateWrapper(db_path_override or config.state_db_path)
    
    scanner = Scanner(config)
    
    pos_mgr = PositionManager(config, bankroll_usd=config.max_total_exposure_usd)
    
    logger.info(
        "Event Trader (IMPROVED) started | mode=%s kelly=%s min_edge=%.1f%%",
        config.mode, config.use_kelly_sizing, config.min_edge * 100,
    )
    
    cycle_number = 0
    consecutive_errors = 0
    
    try:
        while True:
            cycle_number += 1
            
            try:
                result = run_once(config, client, scanner, pos_mgr, state, cycle_number)
                consecutive_errors = 0
                
                logger.info(
                    "Cycle complete: scanned=%s signals=%s executed=%s",
                    result.get("markets_scanned", 0),
                    result.get("signals_found", 0),
                    result.get("executed", 0),
                )
                
            except KeyboardInterrupt:
                raise
            except Exception as e:
                consecutive_errors += 1
                backoff = min(20.0 * consecutive_errors, 300.0)
                logger.exception(
                    "Cycle error #%d (%d consecutive), backing off %.1fs",
                    cycle_number, consecutive_errors, backoff,
                )
                time.sleep(backoff)
                continue
            
            # Fast polling if we have positions to monitor, otherwise use config
            poll_time = config.poll_interval_seconds
            if result.get("exits", 0) > 0 or len(state.get_open_positions()) > 0:
                poll_time = min(2.0, config.poll_interval_seconds)
            
            time.sleep(poll_time)
            
    except KeyboardInterrupt:
        logger.info("Interrupted after %d cycles", cycle_number)
    finally:
        state.close()
        logger.info("Event Trader (IMPROVED) stopped")














