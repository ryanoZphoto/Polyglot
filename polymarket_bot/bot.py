from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List

from .config import BotConfig
from .executor import DryRunExecutor, ExecutionResult, LiveExecutor
from .polymarket import PolymarketDataClient, extract_no_quote
from .strategy import find_no_basket_arbitrage
from .types import MarketLeg, Opportunity, ParsedMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    scanned_markets: int
    eligible_groups: int
    opportunities: List[Opportunity]
    executions: List[ExecutionResult]


def _market_is_eligible(market: ParsedMarket, config: BotConfig, last_trade_at: Dict[str, float]) -> bool:
    if not market.active or market.closed:
        return False
    if not market.accepting_orders:
        return False
    if not market.enable_orderbook:
        return False
    if market.liquidity < config.min_liquidity:
        return False
    if len(market.outcomes) < 2:
        return False

    if config.sports_only:
        text = f"{market.question} {market.event_title or ''}".lower()
        sports_markers = (
            "nba",
            "nfl",
            "mlb",
            "nhl",
            "uefa",
            "premier league",
            "college",
            "atp",
            "wta",
            "fifa",
            "soccer",
            "baseball",
            "basketball",
            "tennis",
            "golf",
            "boxing",
            "ufc",
            "mma",
            "formula 1",
            "f1",
            " vs ",
        )
        if not any(marker in text for marker in sports_markers):
            return False

    if config.include_keywords:
        lower_text = f"{market.question} {market.event_title or ''}".lower()
        if not any(k in lower_text for k in config.include_keywords):
            return False

    last = last_trade_at.get(market.id)
    if last is not None and (time.time() - last) < config.market_cooldown_seconds:
        return False
    return True


def _executor_from_config(config: BotConfig):
    if config.dry_run:
        return DryRunExecutor()
    if not config.private_key:
        raise ValueError("BOT_MODE=live requires PM_PRIVATE_KEY to be set.")
    if config.signature_type in {1, 2} and not config.funder:
        raise ValueError(
            "BOT_SIGNATURE_TYPE=1/2 requires PM_FUNDER (proxy wallet address) to be set."
        )
    return LiveExecutor(
        clob_host=config.clob_host,
        chain_id=config.chain_id,
        private_key=config.private_key,
        signature_type=config.signature_type,
        api_key=config.api_key,
        api_secret=config.api_secret,
        api_passphrase=config.api_passphrase,
        funder=config.funder,
    )


def _group_key(market: ParsedMarket) -> str | None:
    if not market.event_slug:
        return None
    return f"{market.event_slug}:{market.group_item_title}".lower()


def run_bot_once(config: BotConfig, last_trade_at: Dict[str, float] | None = None) -> RunSummary:
    if last_trade_at is None:
        last_trade_at = {}

    client = PolymarketDataClient(
        gamma_host=config.gamma_host,
        clob_host=config.clob_host,
        timeout_seconds=config.request_timeout_seconds,
    )
    executor = _executor_from_config(config)

    markets = client.fetch_markets(config.scan_limit)

    opportunities: List[Opportunity] = []
    grouped: Dict[str, List[MarketLeg]] = {}
    for market in markets:
        if not _market_is_eligible(market, config, last_trade_at):
            continue
        no_quote = extract_no_quote(market)
        if no_quote is None:
            continue
        no_ask = client.get_best_ask(no_quote.token_id)
        no_quote.best_ask = no_ask
        if no_ask is None:
            continue
        key = _group_key(market)
        if not key:
            continue
        grouped.setdefault(key, []).append(
            MarketLeg(
                market_id=market.market_id,
                market_slug=market.slug,
                market_question=market.question,
                token_id=no_quote.token_id,
                outcome_name=no_quote.name,
                price=no_ask,
                liquidity=market.liquidity,
            )
        )

    for key, legs in grouped.items():
        if len(legs) < config.min_group_size:
            continue
        opp = find_no_basket_arbitrage(
            group_key=key,
            legs=legs,
            min_group_size=config.min_group_size,
            max_group_size=config.max_group_size,
            min_edge=config.min_edge,
            min_profit_usd=config.min_profit_usd,
            max_capital=config.max_capital_per_trade,
            max_bundle_shares=config.max_bundle_shares,
        )
        if opp:
            opportunities.append(opp)

    opportunities = sorted(opportunities, key=lambda o: o.expected_profit_usd, reverse=True)
    selected = opportunities[: config.max_opportunities_per_cycle]

    executions: List[ExecutionResult] = []
    for opp in selected:
        result = executor.execute(opp)
        executions.append(result)
        logger.info(result.message)
        for leg in opp.legs:
            last_trade_at[leg.market_id] = time.time()

    return RunSummary(
        scanned_markets=len(markets),
        eligible_groups=len(grouped),
        opportunities=selected,
        executions=executions,
    )


def run_bot_loop(config: BotConfig) -> None:
    logger.info("Starting polymarket bot mode=%s", config.mode)
    last_trade_at: Dict[str, float] = {}
    while True:
        started = time.time()
        summary = run_bot_once(config, last_trade_at=last_trade_at)
        logger.info(
            "Cycle summary scanned=%s groups=%s exec=%s",
            summary.scanned_markets,
            summary.eligible_groups,
            len(summary.executions),
        )
        elapsed = time.time() - started
        sleep_for = max(0.05, config.poll_interval_seconds - elapsed)
        time.sleep(sleep_for)
