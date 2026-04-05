from __future__ import annotations

import argparse
import logging

from polymarket_bot.bot import run_bot_loop, run_bot_once
from polymarket_bot.config import BotConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket sports arbitrage bot.")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = BotConfig.from_env()
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
