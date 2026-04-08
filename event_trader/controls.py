"""
Live control file -- the UI writes, the bot reads each cycle.

Stored as a small JSON file at ev_controls.json in the working directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CONTROLS_PATH = Path("ev_controls.json")

_DEFAULTS = {
    "paused": False,
    "max_invested_usd": 100.0,
}


def read_controls() -> dict:
    if not _CONTROLS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(_CONTROLS_PATH.read_text(encoding="utf-8"))
        result = dict(_DEFAULTS)
        result.update(data)
        return result
    except Exception:
        logger.debug("failed to read controls file, using defaults")
        return dict(_DEFAULTS)


def write_controls(controls: dict) -> None:
    merged = dict(_DEFAULTS)
    merged.update(controls)
    _CONTROLS_PATH.write_text(
        json.dumps(merged, indent=2) + "\n", encoding="utf-8",
    )
