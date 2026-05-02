"""Entry point for improved event trader."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

from event_trader.data_client import EVDataClient

from event_trader_improved.config import ImprovedEVConfig
from event_trader_improved.positions import PositionManager
from event_trader_improved.runtime import run_loop, run_once
from event_trader_improved.scanner import Scanner
from event_trader_improved.state_wrapper import ImprovedStateWrapper

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Improved event-driven trader bot.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    return parser.parse_args()


def _setup_logging(run_dir: Path, log_json: bool) -> None:
    """Configure logging to both console and file."""
    del log_json  # Reserved for future structured logging support.

    log_file = run_dir / "runtime.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(file_handler)


def _create_run_dir() -> Path:
    """Create timestamped run directory."""
    base = Path("ev_runs_improved")
    base.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base / timestamp
    run_dir.mkdir(exist_ok=True)
    return run_dir


def _runtime_config(config: ImprovedEVConfig, run_dir: Path) -> ImprovedEVConfig:
    return replace(
        config,
        state_db_path=str(run_dir / "state.sqlite3"),
        log_path=str(run_dir / "runtime.log"),
        report_path=str(run_dir / "cycle_report.jsonl"),
    )


def _write_config(config: ImprovedEVConfig, run_dir: Path) -> None:
    (run_dir / "config.json").write_text(
        json.dumps(asdict(config), indent=2) + "\n",
        encoding="utf-8",
    )


def _print_banner(config: ImprovedEVConfig, run_dir: Path) -> None:
    print("=" * 60)
    print("EVENT TRADER - IMPROVED VERSION")
    print("=" * 60)
    print(f"Mode: {config.mode}")
    print(f"Kelly sizing: {config.use_kelly_sizing}")
    print(f"Min edge: {config.min_edge:.1%}")
    print(f"Run directory: {run_dir}")
    print("=" * 60)


def _run_once(config: ImprovedEVConfig) -> None:
    client = EVDataClient(config)
    state = ImprovedStateWrapper(config.state_db_path)
    scanner = Scanner(config)
    pos_mgr = PositionManager(config, bankroll_usd=config.max_total_exposure_usd)
    try:
        result = run_once(config, client, scanner, pos_mgr, state, cycle_number=1)
    finally:
        state.close()

    print(
        f"cycle_id={result.get('cycle_id')} scanned={result.get('markets_scanned', 0)} "
        f"signals={result.get('signals_found', 0)} executed={result.get('executed', 0)} "
        f"exits={result.get('exits', 0)}"
    )


def main() -> None:
    args = parse_args()
    config = ImprovedEVConfig.from_env()
    run_dir = _create_run_dir()
    config = _runtime_config(config, run_dir)

    _setup_logging(run_dir, config.log_json)
    _write_config(config, run_dir)
    _print_banner(config, run_dir)

    try:
        if args.once:
            _run_once(config)
        else:
            run_loop(config)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("fatal error in improved event trader")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

