from __future__ import annotations

from .state import StateStore
from .types import ExecutionResult, Opportunity


def reconcile_trade(state: StateStore, trade_id: str, opp: Opportunity, result: ExecutionResult) -> None:
    """
    Bug 4 fix: reconcile fill data correctly for dry-run vs live modes.

    - dry_run: record a fill with realized_pnl_usd = expected_profit_usd so the dashboard
      shows meaningful simulated PnL data instead of useless zeros.
    - live: do NOT record a fill immediately. The fill record should only be written when
      we receive an actual exchange confirmation (fill webhook / polling). Recording a fill
      immediately with 0 PnL pollutes the daily_realized_pnl_usd check in the risk engine
      and gives a false picture of account performance.
    """
    if result.ok:
        state.update_trade_status(trade_id, "submitted")
        if result.mode == "dry_run":
            # Simulated fill: use the expected profit so dashboard trends are meaningful.
            state.record_fill(
                trade_id=trade_id,
                filled_usd=opp.total_cost_usd,
                realized_pnl_usd=opp.expected_profit_usd,
                mode="dry_run",
            )
        # For live mode: no fill record yet. A separate fill-polling/webhook component
        # should call state.record_fill() once exchange confirms the order was matched.
    else:
        state.update_trade_status(trade_id, "failed")
