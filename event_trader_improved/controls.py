# Copy from event_trader/controls.py
from __future__ import annotations

import json
from pathlib import Path


def read_controls(path: str = "ev_controls_improved.json") -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}