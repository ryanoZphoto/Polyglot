from __future__ import annotations

import json
from pathlib import Path

from .types import CycleResult


class CycleReportWriter:
    def __init__(self, report_path: str):
        self.path = Path(report_path)

    def write(self, result: CycleResult) -> None:
        entry = {
            "cycle_id": result.cycle_id,
            "markets_quoted": result.markets_quoted,
            "orders_posted": result.orders_posted,
            "orders_cancelled": result.orders_cancelled,
            "fills_detected": result.fills_detected,
            "total_pnl_this_cycle": result.total_pnl_this_cycle,
            "diagnostics": result.diagnostics,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
