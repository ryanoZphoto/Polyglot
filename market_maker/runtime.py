from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .config import MMConfig
from .cycle_report import CycleReportWriter
from .data_client import MMDataClient
from .executor import BaseMMExecutor, DryRunMMExecutor, LiveMMExecutor
from .inventory import InventoryManager
from .quoting import QuotingEngine
from .risk import MMRiskEngine
from .state import MMStateStore
from .types import CycleResult, FillRecord, OrderBook, ParsedMarket, Quote

logger = logging.getLogger(__name__)

NEAR_FILL_THRESHOLD = 0.005  # log near-misses within 0.5 cents


def build_executor(config: MMConfig, state: MMStateStore, inventory: InventoryManager) -> BaseMMExecutor:
    if config.dry_run:
        return DryRunMMExecutor(config, state, inventory)
    return LiveMMExecutor(config, state, inventory)


def select_markets(config: MMConfig, all_markets: list[ParsedMarket]) -> list[ParsedMarket]:
    """Filter to quotable binary markets with enough liquidity."""
    selected = []
    for m in all_markets:
        if not m.active or m.closed or not m.accepting_orders:
            continue
        if not m.enable_orderbook:
            continue
        if len(m.outcomes) != 2 or len(m.token_ids) != 2:
            continue
        if m.liquidity < config.min_liquidity:
            continue
        if config.markets != ["auto"] and m.slug not in config.markets:
            continue
        selected.append(m)
    return selected


def fetch_books_parallel(client: MMDataClient, token_ids: list[str],
                         max_workers: int) -> dict[str, OrderBook]:
    books: dict[str, OrderBook] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(client.fetch_order_book, tid): tid for tid in token_ids}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                books[tid] = future.result()
            except Exception:
                logger.warning("Failed to fetch book for %s", tid[:12])
    return books


def _quote_changed(old_order: dict, new_quote: Quote, side: str) -> bool:
    """Check if a quote has moved enough to warrant cancelling and re-posting."""
    old_price = float(old_order["price"])
    new_price = new_quote.bid_price if side == "BUY" else new_quote.ask_price
    return abs(old_price - new_price) >= 0.001


def _log_near_fills(state: "MMStateStore", books: dict[str, OrderBook]) -> list[dict]:
    """Log orders that are close to filling -- the best debugging signal."""
    near: list[dict] = []
    all_open = state.get_open_orders()
    for row in all_open:
        token_id = row["token_id"]
        book = books.get(token_id)
        if book is None:
            continue
        side = row["side"]
        order_price = float(row["price"])
        if side == "BUY" and book.best_ask is not None:
            gap = book.best_ask - order_price
            if gap < NEAR_FILL_THRESHOLD:
                near.append({
                    "side": side, "token": token_id[:20], "our": order_price,
                    "book_ask": book.best_ask, "gap": round(gap, 4),
                })
        elif side == "SELL" and book.best_bid is not None:
            gap = order_price - book.best_bid
            if gap < NEAR_FILL_THRESHOLD:
                near.append({
                    "side": side, "token": token_id[:20], "our": order_price,
                    "book_bid": book.best_bid, "gap": round(gap, 4),
                })
    near.sort(key=lambda x: x["gap"])
    for n in near[:10]:
        logger.info("near_fill: %s", n)
    return near


def _build_book_summary(books: dict[str, OrderBook]) -> dict:
    """Summary stats for all fetched books."""
    spreads = []
    one_sided = 0
    empty = 0
    for book in books.values():
        if not book.bids and not book.asks:
            empty += 1
        elif not book.bids or not book.asks:
            one_sided += 1
        else:
            s = book.spread
            if s is not None:
                spreads.append(s)
    return {
        "total": len(books),
        "empty": empty,
        "one_sided": one_sided,
        "two_sided": len(spreads),
        "avg_spread": round(sum(spreads) / len(spreads), 5) if spreads else 0,
        "min_spread": round(min(spreads), 5) if spreads else 0,
        "max_spread": round(max(spreads), 5) if spreads else 0,
        "median_spread": round(sorted(spreads)[len(spreads) // 2], 5) if spreads else 0,
    }


def _log_inventory_snapshot(state: "MMStateStore") -> dict:
    all_inv = state.get_all_inventory()
    positions = 0
    total_long = 0.0
    total_short = 0.0
    total_exposure = 0.0
    for inv in all_inv:
        pos = float(inv["position_shares"])
        avg = float(inv["avg_entry_price"])
        if abs(pos) > 0.01:
            positions += 1
            if pos > 0:
                total_long += pos * avg
            else:
                total_short += abs(pos) * avg
            total_exposure += abs(pos) * avg
    snapshot = {
        "active_positions": positions,
        "long_exposure_usd": round(total_long, 4),
        "short_exposure_usd": round(total_short, 4),
        "total_exposure_usd": round(total_exposure, 4),
        "daily_pnl_usd": round(state.daily_realized_pnl(), 4),
    }
    return snapshot


def run_once(config: MMConfig, client: MMDataClient, quoting: QuotingEngine,
             risk: MMRiskEngine, executor: BaseMMExecutor, state: MMStateStore,
             reporter: CycleReportWriter, cycle_number: int) -> CycleResult:
    """Execute a single market-making cycle with comprehensive logging."""
    t_start = time.monotonic()
    cycle_id = f"mm_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # ── pre-cycle risk ──
    risk_check = risk.pre_cycle_check()
    if not risk_check.allowed:
        logger.warning("cycle %s BLOCKED: %s", cycle_id, risk_check.reason)
        result = CycleResult(
            cycle_id=cycle_id, markets_quoted=0, orders_posted=0,
            orders_cancelled=0, fills_detected=0, total_pnl_this_cycle=0.0,
            diagnostics={"blocked": risk_check.reason, "cycle_number": cycle_number},
        )
        reporter.write(result)
        state.record_cycle(cycle_id, 0, 0, 0, 0, 0.0)
        return result

    # ── fetch markets ──
    t_markets = time.monotonic()
    all_markets = client.fetch_active_markets()
    markets = select_markets(config, all_markets)
    t_markets_done = time.monotonic()

    all_token_ids = []
    for m in markets:
        all_token_ids.extend(m.token_ids)

    logger.info(
        "cycle %s (#%d): %d markets selected from %d active  (%.1fs)",
        cycle_id, cycle_number, len(markets), len(all_markets),
        t_markets_done - t_markets,
    )

    # ── fetch books ──
    t_books = time.monotonic()
    books = fetch_books_parallel(client, all_token_ids, config.max_workers)
    t_books_done = time.monotonic()

    book_summary = _build_book_summary(books)
    logger.info(
        "books: %d fetched in %.1fs  spread min=%.4f avg=%.4f max=%.4f  empty=%d one_sided=%d",
        book_summary["total"], t_books_done - t_books,
        book_summary["min_spread"], book_summary["avg_spread"], book_summary["max_spread"],
        book_summary["empty"], book_summary["one_sided"],
    )

    # ── STEP 1: check fills ──
    t_fills = time.monotonic()
    fills = executor.check_fills(books)
    t_fills_done = time.monotonic()

    if fills:
        for f in fills:
            logger.info(
                "FILL: %s %s %.0f@%.3f pnl=$%.4f  token=%s  order=%s",
                f.side, f.mode, f.size, f.price, f.realized_pnl,
                f.token_id[:20], f.order_id[:20],
            )
    else:
        near = _log_near_fills(state, books)
        if not near:
            open_count = state.count_open_orders()
            logger.info("no fills, no near-fills  (%d open orders)", open_count)

    # ── STEP 2: generate quotes ──
    t_quote = time.monotonic()
    new_quotes: dict[str, Quote] = {}
    skipped_no_book = 0
    skipped_no_quote = 0
    for m in markets:
        for i, token_id in enumerate(m.token_ids):
            book = books.get(token_id)
            if book is None:
                skipped_no_book += 1
                continue
            outcome = m.outcomes[i] if i < len(m.outcomes) else "?"
            q = quoting.generate_quote(m.market_id, token_id, outcome, book)
            if q is not None:
                new_quotes[token_id] = q
            else:
                skipped_no_quote += 1
    t_quote_done = time.monotonic()

    logger.info(
        "quoting: %d generated, %d skipped_no_book, %d skipped_no_quote  (%.3fs)",
        len(new_quotes), skipped_no_book, skipped_no_quote,
        t_quote_done - t_quote,
    )

    # ── STEP 3: cancel changed quotes ──
    cancelled = 0
    cancel_reasons: dict[str, int] = {"price_moved": 0, "no_new_quote": 0}
    for m in markets:
        open_orders = state.get_open_orders(m.market_id)
        for row in open_orders:
            token_id = row["token_id"]
            side = row["side"]
            new_q = new_quotes.get(token_id)
            if new_q is None:
                state.update_order_status(row["order_id"], "cancelled")
                cancelled += 1
                cancel_reasons["no_new_quote"] += 1
            elif _quote_changed(dict(row), new_q, side):
                state.update_order_status(row["order_id"], "cancelled")
                cancelled += 1
                cancel_reasons["price_moved"] += 1

    if cancelled:
        logger.info("cancelled %d orders: %s", cancelled, cancel_reasons)

    # ── STEP 4: post new orders ──
    t_post = time.monotonic()
    quotes_to_post: list[Quote] = []
    orders_posted_this_cycle = 0
    skipped_existing = 0
    skipped_limit = 0
    skipped_risk = 0
    for token_id, q in new_quotes.items():
        existing = state.get_open_orders(q.market_id)
        existing_for_token = {r["side"] for r in existing if r["token_id"] == token_id}

        needs_post = "BUY" not in existing_for_token or "SELL" not in existing_for_token
        if not needs_post:
            skipped_existing += 1
            state.record_quote(
                cycle_id, q.market_id, q.token_id, q.outcome,
                q.bid_price, q.bid_size, q.ask_price, q.ask_size,
                q.fair_value, q.spread, q.skew_applied,
            )
            continue

        if orders_posted_this_cycle >= config.max_open_orders:
            skipped_limit += 1
            continue

        size_usd = q.bid_price * q.bid_size
        order_risk = risk.pre_order_check(q.market_id, token_id, size_usd)
        if not order_risk.allowed:
            skipped_risk += 1
            logger.debug("order blocked: %s  reason=%s", token_id[:16], order_risk.reason)
            continue

        quotes_to_post.append(q)
        orders_posted_this_cycle += 2
        state.record_quote(
            cycle_id, q.market_id, q.token_id, q.outcome,
            q.bid_price, q.bid_size, q.ask_price, q.ask_size,
            q.fair_value, q.spread, q.skew_applied,
        )

    orders = executor.post_quotes(quotes_to_post, cycle_id)
    t_post_done = time.monotonic()

    if orders:
        logger.info(
            "posted %d orders (%d quotes) in %.3fs  skip_existing=%d skip_limit=%d skip_risk=%d",
            len(orders), len(quotes_to_post), t_post_done - t_post,
            skipped_existing, skipped_limit, skipped_risk,
        )

    # ── inventory snapshot ──
    inv_snapshot = _log_inventory_snapshot(state)
    if inv_snapshot["active_positions"] > 0:
        logger.info("inventory: %s", inv_snapshot)

    # ── cycle result ──
    total_pnl = sum(f.realized_pnl for f in fills)
    t_total = time.monotonic() - t_start

    result = CycleResult(
        cycle_id=cycle_id,
        markets_quoted=len(new_quotes),
        orders_posted=len(orders),
        orders_cancelled=cancelled,
        fills_detected=len(fills),
        total_pnl_this_cycle=round(total_pnl, 6),
        diagnostics={
            "cycle_number": cycle_number,
            "mode": config.mode,
            "total_markets": len(all_markets),
            "selected_markets": len(markets),
            "books_fetched": len(books),
            "book_summary": book_summary,
            "quotes_generated": len(new_quotes),
            "quotes_posted": len(quotes_to_post),
            "quotes_unchanged": skipped_existing,
            "skip_no_book": skipped_no_book,
            "skip_no_quote": skipped_no_quote,
            "skip_limit": skipped_limit,
            "skip_risk": skipped_risk,
            "cancel_reasons": cancel_reasons,
            "inventory": inv_snapshot,
            "timing_seconds": {
                "total": round(t_total, 2),
                "fetch_markets": round(t_markets_done - t_markets, 2),
                "fetch_books": round(t_books_done - t_books, 2),
                "check_fills": round(t_fills_done - t_fills, 2),
                "generate_quotes": round(t_quote_done - t_quote, 2),
                "post_orders": round(t_post_done - t_post, 2),
            },
            "fills": [
                {"side": f.side, "price": f.price, "size": f.size,
                 "pnl": f.realized_pnl, "token": f.token_id[:20]}
                for f in fills
            ],
        },
    )

    state.record_cycle(
        cycle_id, result.markets_quoted, result.orders_posted,
        result.orders_cancelled, result.fills_detected, result.total_pnl_this_cycle,
    )
    reporter.write(result)

    logger.info(
        "cycle %s (#%d) done in %.1fs: quoted=%d posted=%d cancelled=%d fills=%d pnl=$%.4f  open=%d",
        cycle_id, cycle_number, t_total,
        result.markets_quoted, result.orders_posted,
        result.orders_cancelled, result.fills_detected, result.total_pnl_this_cycle,
        state.count_open_orders(),
    )
    return result


def run_loop(config: MMConfig, db_path_override: str | None = None,
             report_path_override: str | None = None) -> None:
    """Main bot loop -- runs until interrupted."""
    db_path = db_path_override or config.state_db_path
    report_path = report_path_override or config.report_path

    state = MMStateStore(db_path)
    client = MMDataClient(config)
    inventory = InventoryManager(config, state)
    quoting_eng = QuotingEngine(config, inventory)
    risk = MMRiskEngine(config, state)
    executor = build_executor(config, state, inventory)
    reporter = CycleReportWriter(report_path)

    logger.info(
        "market maker started  mode=%s  poll=%.1fs  half_spread=%.3f  quote_size=%.0f  db=%s",
        config.mode, config.poll_interval_seconds, config.half_spread, config.quote_size, db_path,
    )

    cycle_number = 0
    consecutive_errors = 0
    while True:
        cycle_number += 1
        try:
            run_once(config, client, quoting_eng, risk, executor, state, reporter, cycle_number)
            consecutive_errors = 0
        except KeyboardInterrupt:
            logger.info("interrupted, shutting down after %d cycles", cycle_number)
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

    # Final summary
    total_fills = state.total_fills()
    total_cycles = state.cycle_count()
    daily_pnl = state.daily_realized_pnl()
    exposure = state.total_exposure_usd()
    logger.info(
        "SESSION SUMMARY: cycles=%d fills=%d daily_pnl=$%.4f exposure=$%.2f",
        total_cycles, total_fills, daily_pnl, exposure,
    )

    state.close()
    logger.info("market maker stopped")
