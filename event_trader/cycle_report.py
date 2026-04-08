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
            "cycle_number": result.cycle_number,
            "markets_scanned": result.markets_scanned,
            "signals_found": result.signals_found,
            "entries_placed": result.entries_placed,
            "exits_placed": result.exits_placed,
            "fills_detected": result.fills_detected,
            "positions_open": result.positions_open,
            "total_unrealized_pnl": result.total_unrealized_pnl,
            "total_realized_pnl": result.total_realized_pnl,
            "diagnostics": result.diagnostics,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
