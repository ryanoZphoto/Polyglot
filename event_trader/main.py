"""
Event-Driven Trader Bot -- entry point.

Usage:  python -m event_trader.main [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import EVConfig
from .cycle_report import CycleReportWriter
from .data_client import EVDataClient
from .logging_utils import configure_logging
from .positions import PositionManager
from .risk import EVRiskEngine
from .runtime import build_executor, run_loop, run_once
from .scanner import OpportunityScanner
from .state import EVStateStore

logger = logging.getLogger(__name__)

_RUNS_DIR = Path("ev_runs")


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


def _log_config(config: EVConfig, run_dir: Path) -> None:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Event-driven trader bot.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    return parser.parse_args()


def _run_once(config: EVConfig, db_file: str, report_file: str) -> None:
    state = EVStateStore(db_file)
    client = EVDataClient(config)
    scanner = OpportunityScanner(config, client, state)
    pos_mgr = PositionManager(config, state)
    risk = EVRiskEngine(config, state)
    executor = build_executor(config, state)
    reporter = CycleReportWriter(report_file)
    try:
        result = run_once(
            config,
            client,
            scanner,
            pos_mgr,
            risk,
            executor,
            state,
            reporter,
            cycle_number=1,
        )
    finally:
        state.close()

    print(
        f"cycle_id={result.cycle_id} scanned={result.markets_scanned} "
        f"signals={result.signals_found} entries={result.entries_placed} "
        f"exits={result.exits_placed}"
    )


def main() -> None:
    args = parse_args()
    config = EVConfig.from_env()
    run_dir = _create_run_dir()

    log_file = str(run_dir / "runtime.log")
    report_file = str(run_dir / "cycle_report.jsonl")
    db_file = str(run_dir / "state.sqlite3")

    configure_logging(log_file, config.log_json)

    logger.info("=" * 70)
    logger.info("EVENT TRADER  mode=%s  run_dir=%s", config.mode, run_dir)
    logger.info("=" * 70)

    _log_config(config, run_dir)

    try:
        if args.once:
            _run_once(config, db_file=db_file, report_file=report_file)
        else:
            run_loop(config, db_path_override=db_file, report_path_override=report_file)
    except Exception:
        logger.exception("fatal error in event trader")
        sys.exit(1)


if __name__ == "__main__":
    main()
