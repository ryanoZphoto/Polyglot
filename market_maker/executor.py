from __future__ import annotations

import abc
import logging
import uuid
from typing import Any

from .config import MMConfig
from .inventory import InventoryManager
from .state import MMStateStore
from .types import FillRecord, MMOrder, OrderBook, Quote, RiskDecision

logger = logging.getLogger(__name__)


class BaseMMExecutor(abc.ABC):
    def __init__(self, config: MMConfig, state: MMStateStore, inventory: InventoryManager):
        self.config = config
        self.state = state
        self.inventory = inventory

    @abc.abstractmethod
    def post_quotes(self, quotes: list[Quote], cycle_id: str) -> list[MMOrder]:
        ...

    @abc.abstractmethod
    def cancel_stale_orders(self, market_id: str) -> int:
        ...

    @abc.abstractmethod
    def check_fills(self, books: dict[str, OrderBook]) -> list[FillRecord]:
        ...


class DryRunMMExecutor(BaseMMExecutor):
    """
    Simulates everything without calling the CLOB API.

    Posts orders to the local DB as 'simulated'. On each cycle, checks whether
    the real order book would have crossed our simulated quote prices, and if
    so, records a simulated fill.
    """

    def post_quotes(self, quotes: list[Quote], cycle_id: str) -> list[MMOrder]:
        orders: list[MMOrder] = []
        for q in quotes:
            for side, price, size in [("BUY", q.bid_price, q.bid_size), ("SELL", q.ask_price, q.ask_size)]:
                oid = f"sim_{uuid.uuid4().hex[:16]}"
                order = MMOrder(
                    order_id=oid,
                    market_id=q.market_id,
                    token_id=q.token_id,
                    side=side,
                    price=price,
                    size=size,
                    status="simulated",
                    mode="dry_run",
                )
                self.state.record_order(
                    oid, cycle_id, q.market_id, q.token_id,
                    side, price, size, "simulated", "dry_run",
                )
                orders.append(order)
        return orders

    def cancel_stale_orders(self, market_id: str) -> int:
        open_orders = self.state.get_open_orders(market_id)
        count = 0
        for row in open_orders:
            self.state.update_order_status(row["order_id"], "cancelled")
            count += 1
        return count

    def check_fills(self, books: dict[str, OrderBook]) -> list[FillRecord]:
        """
        Check all simulated orders against real book to detect simulated fills.

        A simulated BUY fills if the real best ask drops to or below our bid price.
        A simulated SELL fills if the real best bid rises to or above our ask price.
        """
        fills: list[FillRecord] = []
        all_open = self.state.get_open_orders()
        for row in all_open:
            token_id = row["token_id"]
            book = books.get(token_id)
            if book is None:
                continue

            side = row["side"]
            order_price = float(row["price"])
            order_size = float(row["size"])
            filled = False

            if side == "BUY" and book.best_ask is not None:
                if book.best_ask <= order_price:
                    filled = True
            elif side == "SELL" and book.best_bid is not None:
                if book.best_bid >= order_price:
                    filled = True

            if filled:
                fid = f"simfill_{uuid.uuid4().hex[:16]}"
                market_id = row["market_id"]

                pnl = 0.0
                inv = self.state.get_inventory(token_id)
                if side == "SELL" and inv is not None:
                    avg_entry = float(inv["avg_entry_price"])
                    if avg_entry > 0:
                        pnl = (order_price - avg_entry) * order_size

                fill = FillRecord(
                    fill_id=fid, order_id=row["order_id"], market_id=market_id,
                    token_id=token_id, side=side, price=order_price, size=order_size,
                    fee_usd=0.0, rebate_usd=0.0, realized_pnl=round(pnl, 6), mode="dry_run",
                )

                self.state.record_fill(
                    fid, row["order_id"], market_id, token_id,
                    side, order_price, order_size, 0.0, 0.0, pnl, "dry_run",
                )
                self.state.update_order_status(row["order_id"], "filled")

                outcome = ""
                if inv is not None:
                    outcome = inv["outcome"]
                self.inventory.record_fill(token_id, market_id, outcome, side, order_price, order_size)

                fills.append(fill)

                book_bid = book.best_bid
                book_ask = book.best_ask
                book_spread = book.spread
                inv_pos = float(inv["position_shares"]) if inv else 0
                logger.info(
                    "SIM_FILL: %s %s %.0f@%.3f pnl=$%.4f | "
                    "book bid=%.3f ask=%.3f spread=%.4f | "
                    "inv=%.1f token=%s order=%s",
                    side, "BUY_filled" if side == "BUY" else "SELL_filled",
                    order_size, order_price, pnl,
                    book_bid or 0, book_ask or 0, book_spread or 0,
                    inv_pos, token_id[:20], row["order_id"][:20],
                )

        return fills


class LiveMMExecutor(BaseMMExecutor):
    """
    Posts real GTC limit orders via py_clob_client.

    Requires PM_API_KEY/SECRET/PASSPHRASE and PM_PRIVATE_KEY to be set.
    """

    def __init__(self, config: MMConfig, state: MMStateStore, inventory: InventoryManager):
        super().__init__(config, state, inventory)
        self._clob_client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=self.config.api_key or "",
                api_secret=self.config.api_secret or "",
                api_passphrase=self.config.api_passphrase or "",
            )
            self._clob_client = ClobClient(
                self.config.clob_host,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.funder,
                creds=creds,
            )
        except ImportError:
            logger.error("py_clob_client not installed; live execution will fail")
        except Exception:
            logger.exception("Failed to initialize CLOB client")

    def post_quotes(self, quotes: list[Quote], cycle_id: str) -> list[MMOrder]:
        if self._clob_client is None:
            logger.error("CLOB client not initialized")
            return []

        from py_clob_client.order_builder.constants import BUY, SELL

        orders: list[MMOrder] = []
        for q in quotes:
            for side_str, pm_side, price, size in [
                ("BUY", BUY, q.bid_price, q.bid_size),
                ("SELL", SELL, q.ask_price, q.ask_size),
            ]:
                try:
                    signed = self._clob_client.create_order({
                        "token_id": q.token_id,
                        "price": price,
                        "size": size,
                        "side": pm_side,
                    })
                    resp = self._clob_client.post_order(signed, "GTC")
                    oid = str(resp.get("orderID", resp.get("id", uuid.uuid4().hex[:16])))
                    order = MMOrder(
                        order_id=oid, market_id=q.market_id, token_id=q.token_id,
                        side=side_str, price=price, size=size, status="submitted", mode="live",
                    )
                    self.state.record_order(
                        oid, cycle_id, q.market_id, q.token_id,
                        side_str, price, size, "submitted", "live",
                    )
                    orders.append(order)
                    logger.info("live_order: %s %s %.0f @ %.3f  id=%s", side_str, q.token_id[:12], size, price, oid)
                except Exception:
                    logger.exception("Failed to post %s order for %s", side_str, q.token_id[:12])
        return orders

    def cancel_stale_orders(self, market_id: str) -> int:
        open_orders = self.state.get_open_orders(market_id)
        count = 0
        for row in open_orders:
            oid = row["order_id"]
            try:
                if self._clob_client is not None:
                    self._clob_client.cancel(oid)
                self.state.update_order_status(oid, "cancelled")
                count += 1
            except Exception:
                logger.exception("Failed to cancel order %s", oid)
        return count

    def check_fills(self, books: dict[str, OrderBook]) -> list[FillRecord]:
        """
        In live mode, we poll open orders and check if they've been filled
        by looking at the CLOB API order status. For now, we use the same
        book-crossing heuristic as dry run but mark as live.
        """
        fills: list[FillRecord] = []
        all_open = self.state.get_open_orders()
        for row in all_open:
            token_id = row["token_id"]
            book = books.get(token_id)
            if book is None:
                continue

            side = row["side"]
            order_price = float(row["price"])
            order_size = float(row["size"])
            filled = False

            if side == "BUY" and book.best_ask is not None and book.best_ask <= order_price:
                filled = True
            elif side == "SELL" and book.best_bid is not None and book.best_bid >= order_price:
                filled = True

            if filled:
                fid = f"fill_{uuid.uuid4().hex[:16]}"
                market_id = row["market_id"]

                pnl = 0.0
                inv = self.state.get_inventory(token_id)
                if side == "SELL" and inv is not None:
                    avg_entry = float(inv["avg_entry_price"])
                    if avg_entry > 0:
                        pnl = (order_price - avg_entry) * order_size

                self.state.record_fill(
                    fid, row["order_id"], market_id, token_id,
                    side, order_price, order_size, 0.0, 0.0, pnl, "live",
                )
                self.state.update_order_status(row["order_id"], "filled")

                outcome = inv["outcome"] if inv is not None else ""
                self.inventory.record_fill(token_id, market_id, outcome, side, order_price, order_size)

                fills.append(FillRecord(
                    fill_id=fid, order_id=row["order_id"], market_id=market_id,
                    token_id=token_id, side=side, price=order_price, size=order_size,
                    fee_usd=0.0, rebate_usd=0.0, realized_pnl=round(pnl, 6), mode="live",
                ))
                logger.info("live_fill: %s %s %.0f @ %.3f  pnl=%.4f", side, token_id[:12], order_size, order_price, pnl)

        return fills
