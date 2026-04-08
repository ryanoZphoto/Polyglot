from __future__ import annotations

import abc
import logging
import uuid
from typing import Any

from .config import EVConfig
from .state import EVStateStore
from .types import FillRecord, OrderBook, Signal, TradeOrder

logger = logging.getLogger(__name__)


class BaseEVExecutor(abc.ABC):
    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    @abc.abstractmethod
    def buy_entry(self, signal: Signal, cycle_id: str) -> TradeOrder | None:
        """Buy contracts to open a new position."""
        ...

    @abc.abstractmethod
    def sell_exit(self, exit_info: dict, cycle_id: str) -> FillRecord | None:
        """Sell contracts to close a position."""
        ...


class DryRunEVExecutor(BaseEVExecutor):
    """
    Simulates trades using real book prices but no actual orders.

    Entry: "buys" at the current best ask price.
    Exit: "sells" at the current best bid price.
    """

    def buy_entry(self, signal: Signal, cycle_id: str) -> TradeOrder | None:
        entry_price = signal.entry_price
        budget = signal.sized_usd
        contracts = min(
            budget / entry_price,
            self.config.max_contracts_per_entry,
        )
        contracts = max(1.0, round(contracts, 2))
        cost = entry_price * contracts

        oid = f"sim_buy_{uuid.uuid4().hex[:12]}"
        pid = f"pos_{uuid.uuid4().hex[:12]}"

        order = TradeOrder(
            order_id=oid,
            position_id=pid,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side="BUY",
            price=entry_price,
            size=contracts,
            status="simulated",
            mode="dry_run",
            reason="entry",
        )

        self.state.record_order(
            oid, pid, cycle_id, signal.market_id, signal.token_id,
            "BUY", entry_price, contracts, "simulated", "dry_run", "entry",
        )

        self.state.open_position(
            pid, signal.market_id, signal.token_id,
            signal.outcome, signal.question,
            entry_price, contracts, round(cost, 4),
            signal.target_price, signal.stop_loss_price, "dry_run",
        )

        fid = f"simfill_buy_{uuid.uuid4().hex[:12]}"
        self.state.record_fill(
            fid, oid, pid, signal.market_id, signal.token_id,
            "BUY", entry_price, contracts, round(cost, 4), 0.0, 0.0, "dry_run",
        )

        logger.info(
            "DRY_BUY [%s]: %.0f contracts @ $%.3f = $%.2f  %s  %s",
            signal.tier, contracts, entry_price, cost,
            signal.outcome, signal.question[:60],
        )

        return order

    def sell_exit(self, exit_info: dict, cycle_id: str) -> FillRecord | None:
        position_id = exit_info["position_id"]
        token_id = exit_info["token_id"]
        contracts = exit_info["contracts"]
        current_price = exit_info["current_price"]
        entry_price = exit_info["entry_price"]
        cost_basis = exit_info["cost_basis"]
        reason = exit_info["reason"]

        sell_value = current_price * contracts
        realized_pnl = sell_value - cost_basis

        oid = f"sim_sell_{uuid.uuid4().hex[:12]}"
        fid = f"simfill_sell_{uuid.uuid4().hex[:12]}"

        self.state.record_order(
            oid, position_id, cycle_id, exit_info["market_id"], token_id,
            "SELL", current_price, contracts, "simulated", "dry_run", reason,
        )

        self.state.record_fill(
            fid, oid, position_id, exit_info["market_id"], token_id,
            "SELL", current_price, contracts, round(sell_value, 4),
            0.0, round(realized_pnl, 6), "dry_run",
        )

        self.state.close_position(position_id, round(realized_pnl, 6), reason)

        logger.info(
            "DRY_SELL: %s  %.0f contracts @ $%.3f  cost=$%.2f  value=$%.2f  pnl=$%.4f  %s",
            reason, contracts, current_price, cost_basis, sell_value,
            realized_pnl, exit_info.get("question", "")[:60],
        )

        return FillRecord(
            fill_id=fid, order_id=oid, position_id=position_id,
            market_id=exit_info["market_id"], token_id=token_id,
            side="SELL", price=current_price, size=contracts,
            notional_usd=round(sell_value, 4), fee_usd=0.0,
            realized_pnl=round(realized_pnl, 6), mode="dry_run",
        )


class LiveEVExecutor(BaseEVExecutor):
    """Posts real orders via py_clob_client."""

    def __init__(self, config: EVConfig, state: EVStateStore):
        super().__init__(config, state)
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
            logger.error("py_clob_client not installed; live execution disabled")
        except Exception:
            logger.exception("Failed to initialize CLOB client")

    def buy_entry(self, signal: Signal, cycle_id: str) -> TradeOrder | None:
        if self._clob_client is None:
            logger.error("CLOB client not initialized")
            return None

        from py_clob_client.order_builder.constants import BUY

        entry_price = signal.entry_price
        budget = signal.sized_usd
        contracts = min(
            budget / entry_price,
            self.config.max_contracts_per_entry,
        )
        contracts = max(1.0, round(contracts, 2))

        try:
            signed = self._clob_client.create_order({
                "token_id": signal.token_id,
                "price": entry_price,
                "size": contracts,
                "side": BUY,
            })
            resp = self._clob_client.post_order(signed, "GTC")
            oid = str(resp.get("orderID", resp.get("id", uuid.uuid4().hex[:12])))
        except Exception:
            logger.exception("Failed to post BUY order for %s", signal.token_id[:16])
            return None

        pid = f"pos_{uuid.uuid4().hex[:12]}"
        cost = entry_price * contracts

        self.state.record_order(
            oid, pid, cycle_id, signal.market_id, signal.token_id,
            "BUY", entry_price, contracts, "submitted", "live", "entry",
        )
        self.state.open_position(
            pid, signal.market_id, signal.token_id,
            signal.outcome, signal.question,
            entry_price, contracts, round(cost, 4),
            signal.target_price, signal.stop_loss_price, "live",
        )

        logger.info(
            "LIVE_BUY: %.0f @ $%.3f = $%.2f  order=%s  %s",
            contracts, entry_price, cost, oid, signal.question[:60],
        )

        return TradeOrder(
            order_id=oid, position_id=pid,
            market_id=signal.market_id, token_id=signal.token_id,
            side="BUY", price=entry_price, size=contracts,
            status="submitted", mode="live", reason="entry",
        )

    def sell_exit(self, exit_info: dict, cycle_id: str) -> FillRecord | None:
        if self._clob_client is None:
            logger.error("CLOB client not initialized")
            return None

        from py_clob_client.order_builder.constants import SELL

        token_id = exit_info["token_id"]
        contracts = exit_info["contracts"]
        current_price = exit_info["current_price"]
        position_id = exit_info["position_id"]
        cost_basis = exit_info["cost_basis"]
        reason = exit_info["reason"]

        try:
            signed = self._clob_client.create_order({
                "token_id": token_id,
                "price": current_price,
                "size": contracts,
                "side": SELL,
            })
            resp = self._clob_client.post_order(signed, "GTC")
            oid = str(resp.get("orderID", resp.get("id", uuid.uuid4().hex[:12])))
        except Exception:
            logger.exception("Failed to post SELL for %s", token_id[:16])
            return None

        sell_value = current_price * contracts
        realized_pnl = sell_value - cost_basis
        fid = f"fill_sell_{uuid.uuid4().hex[:12]}"

        self.state.record_order(
            oid, position_id, cycle_id, exit_info["market_id"], token_id,
            "SELL", current_price, contracts, "submitted", "live", reason,
        )
        self.state.record_fill(
            fid, oid, position_id, exit_info["market_id"], token_id,
            "SELL", current_price, contracts, round(sell_value, 4),
            0.0, round(realized_pnl, 6), "live",
        )
        self.state.close_position(position_id, round(realized_pnl, 6), reason)

        logger.info(
            "LIVE_SELL: %s  %.0f @ $%.3f  pnl=$%.4f  order=%s",
            reason, contracts, current_price, realized_pnl, oid,
        )

        return FillRecord(
            fill_id=fid, order_id=oid, position_id=position_id,
            market_id=exit_info["market_id"], token_id=token_id,
            side="SELL", price=current_price, size=contracts,
            notional_usd=round(sell_value, 4), fee_usd=0.0,
            realized_pnl=round(realized_pnl, 6), mode="live",
        )
