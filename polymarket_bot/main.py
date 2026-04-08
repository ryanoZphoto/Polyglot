from __future__ import annotations

import argparse
import logging
from pathlib import Path

from polymarket_bot.bot import run_bot_loop, run_bot_once
from polymarket_bot.config import BotConfig
from polymarket_bot.logging_utils import configure_logging
from polymarket_bot.state import StateStore

logger = logging.getLogger(__name__)

# Standard log file location — must match what the dashboard reads.
_LOG_FILE = str(Path(__file__).resolve().parent.parent / "bot_runtime.log")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket sports arbitrage bot.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs.")
    parser.add_argument("--status", action="store_true", help="Print latest persisted cycle summary and exit.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config = BotConfig.from_env()
    # Bug 1 fix: pass log_file so the RotatingFileHandler is actually attached.
    # Without this, the subprocess launched by the dashboard wrote to a black hole.
    configure_logging(debug=args.debug, json_logs=config.log_json, log_file=_LOG_FILE)
    if args.status:
        state = StateStore(config.state_db_path)
        try:
            snapshot = state.exposure_snapshot()
            latest = state.latest_cycle_summary()
            print(
                f"open_exposure_usd={snapshot.open_exposure_usd:.4f} "
                f"daily_realized_pnl_usd={snapshot.daily_realized_pnl_usd:.4f}"
            )
            if latest is None:
                print("latest_cycle=none")
            else:
                print(
                    f"latest_cycle_id={latest['cycle_id']} scanned={latest['scanned_markets']} "
                    f"opportunities={latest['opportunities']} executed={latest['executed']}"
                )
        finally:
            state.close()
        raise SystemExit(0)
    try:
        if args.once:
            result = run_bot_once(config)
            print(
                f"scanned={result.scanned_markets} groups={result.eligible_groups} "
                f"opportunities={len(result.opportunities)}"
            )
            for execution in result.executions:
                print(execution.message)
        else:
            run_bot_loop(config)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user interrupt.")
    except Exception as e:
        # Fallback guard to log unexpected termination (Issue #3)
        logger.critical("Bot loop terminated due to unhandled exception: %s", e, exc_info=True)
        raise SystemExit(1)
