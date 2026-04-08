"""Runtime loop for improved event trader."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from event_trader_improved.config import EVConfig
from event_trader.controls import read_controls
from event_trader.cycle_report import CycleReportWriter
from event_trader.data_client import EVDataClient
from event_trader.executor import BaseEVExecutor, DryRunEVExecutor, LiveEVExecutor
from event_trader_improved.positions import PositionManager  # Use improved
from event_trader.risk import EVRiskEngine
from event_trader_improved.scanner import EVScanner  # Changed from OpportunityScanner
from event_trader.state import EVStateStore
from event_trader.types import CycleResult, OrderBook

logger = logging.getLogger(__name__)


def build_executor(config: EVConfig, state: EVStateStore) -> BaseEVExecutor:
    if config.dry_run:
        return DryRunEVExecutor(config, state)
    return LiveEVExecutor(config, state)


def fetch_books_parallel(client: EVDataClient, token_ids: list[str],
                         max_workers: int) -> dict[str, OrderBook]:
    books: dict[str, OrderBook] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(client.fetch_order_book, tid): tid for tid in token_ids}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                books[tid] = future.result()
            except Exception:
                logger.debug("book fetch failed for %s", tid[:16])
    return books


def run_once(
    config: EVConfig,
    client: EVDataClient,
    scanner: EVScanner,
    pos_mgr: PositionManager,
    risk: EVRiskEngine,
    executor: BaseEVExecutor,
    state: EVStateStore,
    reporter: CycleReportWriter,
    cycle_number: int,
) -> CycleResult:
    """Single cycle - improved version."""
    cycle_id = f"ev_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    t_start = time.monotonic()
    
    # Read controls
    controls = read_controls()
    is_paused = controls.get("paused", False)
    
    # Fetch markets
    t_fetch = time.monotonic()
    all_markets = client.fetch_active_markets(limit=config.scan_limit)  # Changed method name
    logger.info("cycle %s (#%d): fetched %d markets in %.1fs",
                cycle_id, cycle_number, len(all_markets), time.monotonic() - t_fetch)
    
    if is_paused:
        logger.info("PAUSED by user -- monitoring positions only")
    
    # Scan for signals
    t_scan = time.monotonic()
    signals = scanner.scan(all_markets) if not is_paused else []
    logger.info("scan: %d signals found in %.1fs%s", 
                len(signals), time.monotonic() - t_scan,
                " (PAUSED)" if is_paused else "")
    
    for sig in signals[:5]:
        logger.info(
            "SIGNAL [%s $%.0f]: $%.3f  edge=%.1f%%  %s  \"%s\"",
            sig.tier, sig.sized_usd, sig.entry_price,
            sig.confidence * 100,
            sig.outcome, sig.question[:60],
        )
    
    # Execute entries
    t_exec = time.monotonic()
    fills = []
    for sig in signals:
        if risk.can_enter(sig):
            fill = executor.execute_entry(sig)
            if fill:
                fills.append(fill)
                state.record_fill(fill)
    
    logger.info("executed %d entries in %.1fs", len(fills), time.monotonic() - t_exec)
    
    # Monitor positions
    t_monitor = time.monotonic()
    open_positions = state.get_open_positions()
    held_token_ids = [p["token_id"] for p in open_positions]
    
    exits = []
    if held_token_ids:
        position_books = fetch_books_parallel(client, held_token_ids, config.max_workers)
        live_prices = client.fetch_live_prices(held_token_ids)
        
        for pos in open_positions:
            tid = pos["token_id"]
            current_price = live_prices.get(tid, pos["entry_price"])
            
            exit_signal = pos_mgr.check_exit(pos, current_price)
            if exit_signal:
                exit_fill = executor.execute_exit(exit_signal)
                if exit_fill:
                    exits.append(exit_fill)
                    state.record_fill(exit_fill)
    
    logger.info("monitored %d positions, %d exits in %.1fs",
                len(open_positions), len(exits), time.monotonic() - t_monitor)
    
    # Report
    result = CycleResult(
        cycle_id=cycle_id,
        cycle_number=cycle_number,
        markets_scanned=len(all_markets),
        signals_found=len(signals),
        entries_placed=len(fills),
        exits_placed=len(exits),
        fills_detected=len(fills) + len(exits),
        positions_open=state.count_open_positions(),
        total_unrealized_pnl=state.total_unrealized_pnl(),
        total_realized_pnl=state.daily_realized_pnl(),
        diagnostics={
            "mode": config.mode,
            "elapsed_seconds": round(time.monotonic() - t_start, 2),
        },
    )
    reporter.write(result)
    
    state.record_cycle(
        cycle_id, cycle_number, len(all_markets), len(signals),
        len(fills), len(exits), len(fills) + len(exits),
        state.count_open_positions(), 
        state.total_unrealized_pnl(),
        state.daily_realized_pnl(),
    )
    
    return result


def run_loop(config: EVConfig, db_path_override: str | None = None,
             report_path_override: str | None = None) -> None:
    """Main bot loop -- runs until interrupted."""
    db_path = db_path_override or config.state_db_path
    report_path = report_path_override or config.report_path

    state = EVStateStore(db_path)
    client = EVDataClient(config)
    scanner = EVScanner(config, client, state)
    pos_mgr = PositionManager(config, state)
    risk = EVRiskEngine(config, state)
    executor = build_executor(config, state)
    reporter = CycleReportWriter(report_path)

    logger.info(
        "event trader (IMPROVED) started  mode=%s  kelly=%s  min_edge=%.1f%%",
        config.mode, config.use_kelly_sizing, config.min_edge * 100,
    )

    cycle_number = 0
    consecutive_errors = 0
    while True:
        cycle_number += 1
        try:
            run_once(config, client, scanner, pos_mgr, risk, executor,
                     state, reporter, cycle_number)
            consecutive_errors = 0
        except KeyboardInterrupt:
            logger.info("interrupted after %d cycles", cycle_number)
            break
        except Exception:
            consecutive_errors += 1
            backoff = min(60, config.poll_interval_seconds * (2 ** consecutive_errors))
            logger.exception(
                "cycle error #%d (%d consecutive), backing off %.1fs",
                cycle_number, consecutive_errors, backoff,
            )
            time.sleep(backoff)
            continue

        try:
            time.sleep(config.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("interrupted during sleep after %d cycles", cycle_number)
            break

    state.close()
    logger.info("event trader (IMPROVED) stopped")






