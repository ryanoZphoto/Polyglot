from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .state import StateStore
from .types import ExecutionResult, LegExecutionResult, Opportunity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ApiCredsCompat:
    api_key: str
    api_secret: str
    api_passphrase: str


class OrderExecutor(Protocol):
    def execute(self, trade_id: str, opp: Opportunity, state: StateStore) -> ExecutionResult:
        raise NotImplementedError


@dataclass
class DryRunExecutor:
    mode: str = "dry_run"

    def execute(self, trade_id: str, opp: Opportunity, state: StateStore) -> ExecutionResult:
        legs = [
            LegExecutionResult(
                token_id=leg.token_id,
                market_slug=leg.market_slug,
                status="simulated",
                order_id=f"dry-{trade_id}-{idx}",
            )
            for idx, leg in enumerate(opp.legs, start=1)
        ]
        for leg_result in legs:
            state.record_order(
                trade_id=trade_id,
                token_id=leg_result.token_id,
                market_slug=leg_result.market_slug,
                status=leg_result.status,
                order_id=leg_result.order_id,
            )

        message = (
            f"[DRY-RUN] trade_id={trade_id} group='{opp.group_key}' "
            f"legs={len(opp.legs)} shares={opp.bundle_shares:.4f} "
            f"cost={opp.total_cost_usd:.4f} profit={opp.guaranteed_profit_usd:.4f}"
        )
        logger.info(message)
        return ExecutionResult(
            ok=True,
            mode=self.mode,
            message=message,
            trade_id=trade_id,
            submitted_orders=legs,
            errors=None,
        )


class LiveExecutor:
    def __init__(
        self,
        clob_host: str,
        chain_id: int,
        private_key: str,
        funder: str | None = None,
        signature_type: int = 0,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_passphrase: str | None = None,
        client_class: Any | None = None,
        order_args_cls: Any | None = None,
        order_type_cls: Any | None = None,
        buy_side: Any | None = None,
    ):
        self.clob_host = clob_host
        self.chain_id = chain_id

        if client_class is None:
            from py_clob_client.client import ClobClient as client_class  # pragma: no cover

        try:
            from py_clob_client.clob_types import ApiCreds as ApiCredsCls  # pragma: no cover
        except Exception:  # pragma: no cover - only used in isolated tests
            ApiCredsCls = _ApiCredsCompat

        if order_args_cls is None or order_type_cls is None or buy_side is None:
            from py_clob_client.clob_types import OrderArgs, OrderType  # pragma: no cover
            from py_clob_client.order_builder.constants import BUY  # pragma: no cover
            self._order_args_cls = OrderArgs
            self._order_type = OrderType
            self._buy = BUY
        else:
            self._order_args_cls = order_args_cls
            self._order_type = order_type_cls
            self._buy = buy_side

        creds = None
        if api_key and api_secret and api_passphrase:
            creds = ApiCredsCls(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )

        kwargs = {
            "key": private_key,
            "chain_id": chain_id,
            "signature_type": signature_type,
            "creds": creds,
        }
        if funder:
            kwargs["funder"] = funder
        self.client = client_class(clob_host, **kwargs)
        if creds is None:
            self.client.set_api_creds(self.client.create_or_derive_api_creds())

    @staticmethod
    def _validate_price(price: float) -> bool:
        return 0.0 < price < 1.0

    def _cancel_order_if_possible(self, order_id: str) -> None:
        cancel = getattr(self.client, "cancel", None)
        if callable(cancel):
            try:
                cancel(order_id)
            except Exception:  # pragma: no cover - runtime dependent
                logger.warning("Cancel failed for order_id=%s", order_id)

    def _has_sufficient_balance(self, required_usd: float) -> bool:
        getter = getattr(self.client, "get_balance_allowance", None)
        if not callable(getter):
            return True
        try:
            payload = getter()
            if not isinstance(payload, dict):
                return True
            balance = payload.get("balance") or payload.get("available")
            if balance is None:
                return True
            return float(balance) >= required_usd
        except Exception:  # pragma: no cover - runtime dependent
            return True

    def execute(self, trade_id: str, opp: Opportunity, state: StateStore) -> ExecutionResult:
        if not self._has_sufficient_balance(opp.total_cost_usd):
            message = f"[LIVE-ERROR] trade_id={trade_id} blocked=insufficient-balance cost={opp.total_cost_usd:.4f}"
            logger.error(message)
            return ExecutionResult(
                ok=False,
                mode="live",
                message=message,
                trade_id=trade_id,
                submitted_orders=[],
                errors=["insufficient balance"],
            )

        # Execute liquid legs first to reduce partial-fill risk.
        legs = sorted(opp.legs, key=lambda leg: leg.liquidity, reverse=True)
        results: list[LegExecutionResult] = []
        submitted_order_ids: list[str] = []
        errors: list[str] = []

        for leg in legs:
            if not self._validate_price(leg.price):
                msg = f"{leg.market_slug}:invalid-price={leg.price}"
                errors.append(msg)
                leg_result = LegExecutionResult(
                    token_id=leg.token_id,
                    market_slug=leg.market_slug,
                    status="failed",
                    error=msg,
                )
                results.append(leg_result)
                state.record_order(trade_id, leg.token_id, leg.market_slug, leg_result.status, error=msg)
                continue

            try:
                order_args = self._order_args_cls(
                    token_id=leg.token_id,
                    price=leg.price,
                    size=opp.bundle_shares,
                    side=self._buy,
                )
                signed = self.client.create_order(order_args)
                posted = self.client.post_order(signed, self._order_type.GTC)
                order_id = str(posted.get("orderID", "unknown"))
                submitted_order_ids.append(order_id)
                leg_result = LegExecutionResult(
                    token_id=leg.token_id,
                    market_slug=leg.market_slug,
                    status="submitted",
                    order_id=order_id,
                )
                results.append(leg_result)
                state.record_order(
                    trade_id=trade_id,
                    token_id=leg.token_id,
                    market_slug=leg.market_slug,
                    status=leg_result.status,
                    order_id=order_id,
                )
            except Exception as exc:  # pragma: no cover - runtime/API dependent
                msg = f"{leg.market_slug}:{exc}"
                errors.append(msg)
                leg_result = LegExecutionResult(
                    token_id=leg.token_id,
                    market_slug=leg.market_slug,
                    status="failed",
                    error=msg,
                )
                results.append(leg_result)
                state.record_order(trade_id, leg.token_id, leg.market_slug, leg_result.status, error=msg)
                break

        # Best-effort rollback of already-submitted orders if any leg failed.
        if errors and submitted_order_ids:
            for order_id in submitted_order_ids:
                self._cancel_order_if_possible(order_id)
                state.record_order(
                    trade_id=trade_id,
                    token_id="n/a",
                    market_slug="n/a",
                    status="canceled",
                    order_id=order_id,
                )

        ok = len(errors) == 0
        if ok:
            message = (
                f"[LIVE] trade_id={trade_id} submitted={len(submitted_order_ids)}/{len(legs)} "
                f"group='{opp.group_key}' edge={opp.edge:.4f}"
            )
        else:
            message = (
                f"[LIVE-ERROR] trade_id={trade_id} submitted={len(submitted_order_ids)}/{len(legs)} "
                f"group='{opp.group_key}' errors={errors}"
            )
        logger.info(message if ok else message)

        return ExecutionResult(
            ok=ok,
            mode="live",
            message=message,
            trade_id=trade_id,
            submitted_orders=results,
            errors=errors or None,
        )
