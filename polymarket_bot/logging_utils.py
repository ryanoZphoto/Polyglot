from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Any


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(debug: bool, json_logs: bool, log_file: str | None = None) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    date_fmt = "%Y-%m-%d %I:%M:%S %p %Z"
    # Console output
    console = logging.StreamHandler()
    fmt = JsonLogFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt=date_fmt)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File output with rotation (Issue #11)
    if log_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
