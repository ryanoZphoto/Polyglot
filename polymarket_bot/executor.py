from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from py_clob_client.clob_types import ApiCreds

from .types import ExecutionResult, Opportunity

logger = logging.getLogger(__name__)


class OrderExecutor(Protocol):
    def execute(self, opp: Opportunity) -> ExecutionResult:
        raise NotImplementedError


@dataclass
class DryRunExecutor:
    mode: str = "dry_run"

    def execute(self, opp: Opportunity) -> ExecutionResult:
        legs_text = ", ".join(
            f"{leg.outcome_name}@{leg.market_slug}:{leg.price:.4f}" for leg in opp.legs
        )
        message = (
            f"[DRY-RUN] Buy NO basket group='{opp.group_key}' legs={len(opp.legs)} "
            f"shares={opp.bundle_shares:.4f} cost={opp.total_cost_usd:.4f} "
            f"payout={opp.guaranteed_payout_usd:.4f} profit={opp.guaranteed_profit_usd:.4f} "
            f"edge={opp.edge:.4f} legs=[{legs_text}]"
        )
        logger.info(message)
        return ExecutionResult(ok=True, mode=self.mode, message=message)


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
    ):
        self.clob_host = clob_host
        self.chain_id = chain_id
        self.private_key = private_key
        self.funder = funder

        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        self._order_args_cls = OrderArgs
        self._order_type = OrderType
        self._buy = BUY

        creds = None
        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(
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
        self.client = ClobClient(clob_host, **kwargs)

        if creds is None:
            # If creds are not provided, derive/create L2 API creds from the private key (L1 auth).
            self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def execute(self, opp: Opportunity) -> ExecutionResult:
        order_ids: list[str] = []
        errors: list[str] = []
        for leg in opp.legs:
            try:
                order = self._order_args_cls(
                    token_id=leg.token_id,
                    price=leg.price,
                    size=opp.bundle_shares,
                    side=self._buy,
                )
                signed = self.client.create_order(order)
                resp = self.client.post_order(signed, self._order_type.GTC)
                order_id = str(resp.get("orderID", "unknown"))
                order_ids.append(order_id)
            except Exception as exc:  # pragma: no cover - runtime/API dependent
                errors.append(f"{leg.market_slug}:{exc}")

        if errors:
            message = (
                f"[LIVE-ERROR] group='{opp.group_key}' submitted={len(order_ids)}/{len(opp.legs)} "
                f"errors={errors}"
            )
            logger.error(message)
            return ExecutionResult(ok=False, mode="live", message=message)

        message = (
            f"[LIVE] Submitted NO basket group='{opp.group_key}' legs={len(opp.legs)} "
            f"shares={opp.bundle_shares:.4f} edge={opp.edge:.4f} order_ids={order_ids}"
        )
        logger.info(message)
        return ExecutionResult(ok=True, mode="live", message=message)

