"""
Market-Making Bot -- entry point.

Usage:  python -m market_maker.main
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import MMConfig
from .logging_utils import configure_logging
from .runtime import run_loop

logger = logging.getLogger(__name__)

_RUNS_DIR = Path("mm_runs")


def _create_run_dir() -> Path:
    _RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = _RUNS_DIR / stamp
    run_dir.mkdir(exist_ok=True)
    return run_dir


def _mask(value: str | None) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def _log_config(config: MMConfig, run_dir: Path) -> None:
    """Dump full config to log and to a JSON file in the run directory."""
    cfg = asdict(config)
    for key in ("private_key", "api_key", "api_secret", "api_passphrase"):
        cfg[key] = _mask(cfg.get(key))
    cfg["run_dir"] = str(run_dir)
    cfg["python"] = sys.version
    cfg["pid"] = os.getpid()

    (run_dir / "config.json").write_text(
        json.dumps(cfg, indent=2, default=str) + "\n", encoding="utf-8",
    )

    logger.info("config: %s", json.dumps(cfg, default=str))


def main() -> None:
    config = MMConfig.from_env()

    run_dir = _create_run_dir()

    log_file = str(run_dir / "runtime.log")
    report_file = str(run_dir / "cycle_report.jsonl")
    db_file = str(run_dir / "state.sqlite3")

    configure_logging(log_file, config.log_json)

    logger.info("=" * 70)
    logger.info("MARKET MAKER  mode=%s  run_dir=%s", config.mode, run_dir)
    logger.info("=" * 70)

    _log_config(config, run_dir)

    try:
        run_loop(config, db_path_override=db_file, report_path_override=report_file)
    except Exception:
        logger.exception("fatal error in market maker")
        sys.exit(1)


if __name__ == "__main__":
    main()
