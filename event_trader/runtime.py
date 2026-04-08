from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .config import EVConfig
from .controls import read_controls
from .cycle_report import CycleReportWriter
from .data_client import EVDataClient
from .executor import BaseEVExecutor, DryRunEVExecutor, LiveEVExecutor
from .positions import PositionManager
from .risk import EVRiskEngine
from .scanner import OpportunityScanner
from .state import EVStateStore
from .types import CycleResult, OrderBook

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


def run_once(config: EVConfig, client: EVDataClient, scanner: OpportunityScanner,
             pos_mgr: PositionManager, risk: EVRiskEngine, executor: BaseEVExecutor,
             state: EVStateStore, reporter: CycleReportWriter,
             cycle_number: int) -> CycleResult:
    """Execute one scan-buy-monitor-sell cycle."""
    t_start = time.monotonic()
    cycle_id = f"ev_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # ── pre-cycle risk ──
    risk_check = risk.pre_cycle_check()
    if not risk_check.allowed:
        logger.warning("cycle %s BLOCKED: %s", cycle_id, risk_check.reason)
        result = CycleResult(
            cycle_id=cycle_id, cycle_number=cycle_number,
            markets_scanned=0, signals_found=0, entries_placed=0,
            exits_placed=0, fills_detected=0, positions_open=state.count_open_positions(),
            total_unrealized_pnl=state.total_unrealized_pnl(),
            total_realized_pnl=state.daily_realized_pnl(),
            diagnostics={"blocked": risk_check.reason},
        )
        reporter.write(result)
        state.record_cycle(
            cycle_id, cycle_number, 0, 0, 0, 0, 0,
            result.positions_open, result.total_unrealized_pnl, result.total_realized_pnl,
        )
        return result

    # ── STEP 1: fetch markets ──
    t_markets = time.monotonic()
    all_markets = client.fetch_active_markets()
    t_markets_done = time.monotonic()

    logger.info(
        "cycle %s (#%d): fetched %d markets in %.1fs",
        cycle_id, cycle_number, len(all_markets), t_markets_done - t_markets,
    )

    # ── live controls from UI ──
    controls = read_controls()
    is_paused = controls.get("paused", False)
    max_invested = controls.get("max_invested_usd", config.max_total_exposure_usd)
    current_invested = state.total_cost_basis()

    if is_paused:
        logger.info("PAUSED by user -- skipping scan, monitoring positions only")

    # ── STEP 2: scan for opportunities ──
    t_scan = time.monotonic()
    signals = scanner.scan(all_markets) if not is_paused else []
    t_scan_done = time.monotonic()

    logger.info(
        "scan: %d signals found in %.1fs%s",
        len(signals), t_scan_done - t_scan,
        " (PAUSED)" if is_paused else "",
    )

    for sig in signals[:5]:
        logger.info(
            "SIGNAL [%s $%.0f]: $%.3f  conf=%.2f  %s  \"%s\"  %s",
            sig.tier, sig.sized_usd,
            sig.entry_price, sig.confidence, sig.outcome,
            sig.question[:60], sig.reason,
        )

    # ── STEP 3: execute entries -- up to 3 per tier, 9 total, all different markets ──
    max_per_tier = 3
    max_entries_per_cycle = 9
    entries_placed = 0
    entry_fills = 0
    tier_counts_this_cycle: dict[str, int] = {"longshot": 0, "mid": 0, "highprob": 0}
    markets_filled: set[str] = set()

    for sig in signals:
        if entries_placed >= max_entries_per_cycle:
            break

        if tier_counts_this_cycle.get(sig.tier, 0) >= max_per_tier:
            continue

        if sig.market_id in markets_filled:
            continue

        if current_invested + sig.sized_usd > max_invested:
            logger.info(
                "BUDGET_CAP: invested=$%.2f + $%.2f would exceed max=$%.2f, skipping",
                current_invested, sig.sized_usd, max_invested,
            )
            break

        entry_cost = sig.entry_price * min(
            sig.sized_usd / sig.entry_price,
            config.max_contracts_per_entry,
        )
        entry_risk = risk.pre_entry_check(sig.market_id, sig.token_id, entry_cost)
        if not entry_risk.allowed:
            logger.debug("entry blocked: %s  %s", sig.token_id[:16], entry_risk.reason)
            continue

        state.record_signal(
            sig.signal_id, cycle_id, sig.market_id, sig.token_id,
            sig.outcome, sig.question, sig.current_price, sig.entry_price,
            sig.target_price, sig.stop_loss_price, sig.confidence, sig.reason,
            sig.book_bid, sig.book_ask, sig.book_spread,
            sig.bid_depth, sig.ask_depth, acted_on=True,
        )

        order = executor.buy_entry(sig, cycle_id)
        if order is not None:
            entries_placed += 1
            entry_fills += 1
            current_invested += sig.sized_usd
            tier_counts_this_cycle[sig.tier] = tier_counts_this_cycle.get(sig.tier, 0) + 1
            markets_filled.add(sig.market_id)

    # Record non-acted signals too (for analysis)
    for sig in signals:
        if not state.was_signal_recently_seen(sig.token_id, hours=0):
            state.record_signal(
                sig.signal_id, cycle_id, sig.market_id, sig.token_id,
                sig.outcome, sig.question, sig.current_price, sig.entry_price,
                sig.target_price, sig.stop_loss_price, sig.confidence, sig.reason,
                sig.book_bid, sig.book_ask, sig.book_spread,
                sig.bid_depth, sig.ask_depth, acted_on=False,
            )

    # ── build price lookup: Gamma baseline, then overwrite with CLOB last-trade ──
    api_prices: dict[str, float] = {}
    for m in all_markets:
        for i, tid in enumerate(m.token_ids):
            if i < len(m.outcome_prices) and m.outcome_prices[i] > 0:
                api_prices[tid] = m.outcome_prices[i]

    # ── STEP 4: monitor positions and exit ──
    t_monitor = time.monotonic()
    open_positions = state.get_open_positions()
    held_token_ids = [p["token_id"] for p in open_positions]

    position_books: dict[str, OrderBook] = {}
    if held_token_ids:
        position_books = fetch_books_parallel(client, held_token_ids, config.max_workers)
        live_prices = client.fetch_live_prices(held_token_ids)
        upgraded = 0
        for tid, lp in live_prices.items():
            old = api_prices.get(tid)
            if old is not None and abs(lp - old) > 0.0001:
                upgraded += 1
            api_prices[tid] = lp
        if upgraded:
            logger.info("PRICE_UPGRADE: %d/%d tokens got better prices from CLOB",
                        upgraded, len(held_token_ids))

    exit_decisions = pos_mgr.check_exits(position_books, api_prices)
    exits_placed = 0
    exit_fills = 0

    for exit_info in exit_decisions:
        fill = executor.sell_exit(exit_info, cycle_id)
        if fill is not None:
            exits_placed += 1
            exit_fills += 1
    t_monitor_done = time.monotonic()

    # ── portfolio summary ──
    portfolio = pos_mgr.get_portfolio_summary(position_books, api_prices)
    if portfolio["open_positions"] > 0:
        logger.info(
            "PORTFOLIO: %d positions  cost=$%.2f  value=$%.2f  pnl=$%.4f",
            portfolio["open_positions"], portfolio["total_cost_usd"],
            portfolio["total_value_usd"], portfolio["total_unrealized_pnl"],
        )
        for p in portfolio["positions"]:
            logger.info(
                "  POS: %s  entry=%.3f  now=%.3f  pnl=%.1f%%  $%.4f",
                p["outcome"], p["entry"], p["current"], p["pnl_pct"], p["pnl"],
            )

    # ── cycle result ──
    t_total = time.monotonic() - t_start
    total_unrealized = state.total_unrealized_pnl()
    total_realized = state.daily_realized_pnl()

    result = CycleResult(
        cycle_id=cycle_id,
        cycle_number=cycle_number,
        markets_scanned=len(all_markets),
        signals_found=len(signals),
        entries_placed=entries_placed,
        exits_placed=exits_placed,
        fills_detected=entry_fills + exit_fills,
        positions_open=state.count_open_positions(),
        total_unrealized_pnl=round(total_unrealized, 6),
        total_realized_pnl=round(total_realized, 6),
        diagnostics={
            "mode": config.mode,
            "markets_fetched": len(all_markets),
            "signals_top5": [
                {"price": s.entry_price, "conf": s.confidence,
                 "outcome": s.outcome, "q": s.question[:50]}
                for s in signals[:5]
            ],
            "entries_blocked": len(signals) - entries_placed,
            "exits_triggered": len(exit_decisions),
            "portfolio": portfolio,
            "timing_seconds": {
                "total": round(t_total, 2),
                "fetch_markets": round(t_markets_done - t_markets, 2),
                "scan": round(t_scan_done - t_scan, 2),
                "monitor_exits": round(t_monitor_done - t_monitor, 2),
            },
        },
    )

    state.record_cycle(
        cycle_id, cycle_number, result.markets_scanned, result.signals_found,
        result.entries_placed, result.exits_placed, result.fills_detected,
        result.positions_open, result.total_unrealized_pnl, result.total_realized_pnl,
    )
    reporter.write(result)

    logger.info(
        "cycle %s (#%d) done in %.1fs: scanned=%d signals=%d entries=%d exits=%d "
        "open=%d unrealized=$%.4f realized=$%.4f",
        cycle_id, cycle_number, t_total,
        result.markets_scanned, result.signals_found,
        result.entries_placed, result.exits_placed,
        result.positions_open, result.total_unrealized_pnl, result.total_realized_pnl,
    )
    return result


def run_loop(config: EVConfig, db_path_override: str | None = None,
             report_path_override: str | None = None) -> None:
    """Main bot loop -- runs until interrupted."""
    db_path = db_path_override or config.state_db_path
    report_path = report_path_override or config.report_path

    state = EVStateStore(db_path)
    client = EVDataClient(config)
    scanner = OpportunityScanner(config, client, state)
    pos_mgr = PositionManager(config, state)
    risk = EVRiskEngine(config, state)
    executor = build_executor(config, state)
    reporter = CycleReportWriter(report_path)

    logger.info(
        "event trader started  mode=%s  poll=%.1fs  entry_range=$%.2f-$%.2f  "
        "tiers: longshot(<$%.2f)=$%.0f mid=$%.0f highprob(>$%.2f)=$%.0f  "
        "max_positions=%d",
        config.mode, config.poll_interval_seconds,
        config.min_entry_price, config.max_entry_price,
        config.longshot_ceiling, config.longshot_size_usd,
        config.position_size_usd,
        config.highprob_floor, config.highprob_size_usd,
        config.max_positions,
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

    total_fills = state.total_fills()
    total_cycles = state.cycle_count()
    daily_pnl = state.daily_realized_pnl()
    exposure = state.total_exposure_usd()
    logger.info(
        "SESSION SUMMARY: cycles=%d fills=%d daily_pnl=$%.4f exposure=$%.2f open=%d",
        total_cycles, total_fills, daily_pnl, exposure, state.count_open_positions(),
    )

    state.close()
    logger.info("event trader stopped")
