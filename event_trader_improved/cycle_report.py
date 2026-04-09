# Copy from event_trader/cycle_report.py
from __future__ import annotations

import json
from pathlib import Path


class CycleReportWriter:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, data: dict):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")