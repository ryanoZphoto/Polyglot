from __future__ import annotations

from .state import StateStore
from .types import ExecutionResult, Opportunity


def reconcile_trade(state: StateStore, trade_id: str, opp: Opportunity, result: ExecutionResult) -> None:
    if result.ok:
        state.update_trade_status(trade_id, "submitted")
        # Fill data depends on exchange callbacks; record expected values for audit now.
        state.record_fill(
            trade_id=trade_id,
            filled_usd=opp.total_cost_usd,
            realized_pnl_usd=0.0,
        )
    else:
        state.update_trade_status(trade_id, "failed")
