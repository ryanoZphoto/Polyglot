from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import BotConfig
from .data_client import ResilientDataClient
from .types import MarketLeg, Opportunity, ParsedMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaderSignal:
    token_id: str
    slug: str
    outcome_name: str
    observed_price: float
    observed_notional_usd: float
    observed_ts_unix: int


@dataclass(frozen=True)
class LeaderFollowResult:
    opportunities: list[Opportunity]
    diagnostics: dict[str, int]


class LeaderFollowStrategy:
    def __init__(self, config: BotConfig, data_client: ResilientDataClient):
        self.config = config
        self.data_client = data_client
        self.session = requests.Session()

    def build_opportunities(
        self,
        last_trade_at: dict[str, float],
        cached_markets: list[ParsedMarket] | None = None,
    ) -> LeaderFollowResult:
        diagnostics: dict[str, int] = {
            "activity_rows": 0,
            "signals_after_filters": 0,
            "markets_loaded": 0,
            "signal_market_miss": 0,
            "signal_rejected_market_state": 0,
            "signal_rejected_liquidity": 0,
            "signal_rejected_token_mismatch": 0,
            "signal_rejected_cooldown": 0,
            "signal_rejected_quote": 0,
            "signal_rejected_price_drift": 0,
            "signal_rejected_non_positive_edge": 0,
            "signal_rejected_bundle": 0,
            "opportunities_built": 0,
        }
        if not self.config.enable_leader_follow:
            return LeaderFollowResult(opportunities=[], diagnostics=diagnostics)
        wallet = self.config.leader_wallet
        if not wallet:
            return LeaderFollowResult(opportunities=[], diagnostics=diagnostics)

        signals, raw_rows = self._fetch_signals(wallet)
        diagnostics["activity_rows"] = raw_rows
        diagnostics["signals_after_filters"] = len(signals)
        if not signals:
            return LeaderFollowResult(opportunities=[], diagnostics=diagnostics)

        # Issue F fix: reuse the market list already fetched by the scanner if available.
        if cached_markets is not None:
            markets = cached_markets
        else:
            markets = self.data_client.fetch_active_markets(self.config.scan_limit)
        diagnostics["markets_loaded"] = len(markets)
        # Fix (Issue #5): Index by token_id first for precise mapping, fallback to slug.
        # Also normalize slugs to handle trailing/leading whitespace and case differences.
        by_token = {tid: m for m in markets for tid in m.token_ids}
        by_slug = {m.slug.strip().lower(): m for m in markets if m.slug}

        opportunities: list[Opportunity] = []

        for sig in signals:
            market = by_token.get(sig.token_id) or by_slug.get(sig.slug)
            if market is None:
                diagnostics["signal_market_miss"] += 1
                continue
            opp, reject_reason = self._signal_to_opportunity(sig, market, last_trade_at)
            if opp is not None:
                opportunities.append(opp)
                diagnostics["opportunities_built"] += 1
            elif reject_reason:
                key = f"signal_rejected_{reject_reason}"
                if key in diagnostics:
                    diagnostics[key] += 1

        opportunities.sort(key=lambda x: x.expected_profit_usd, reverse=True)
        return LeaderFollowResult(
            opportunities=opportunities[: max(1, self.config.leader_max_signals_per_cycle)],
            diagnostics=diagnostics,
        )

    def _fetch_signals(self, wallet: str) -> tuple[list[LeaderSignal], int]:
        try:
            payload = self.session.get(
                "https://data-api.polymarket.com/activity",
                params={
                    "user": wallet,
                    "limit": max(1, min(self.config.leader_max_signals_per_cycle * 4, 50)),
                    "offset": 0,
                    "sortDirection": "DESC",
                    "type": "TRADE",
                },
                timeout=self.config.request_timeout_seconds,
            )
            payload.raise_for_status()
            data = payload.json()
        except Exception as exc:
            logger.warning("Leader-follow activity fetch failed: %s", exc)
            return [], 0

        if not isinstance(data, list):
            return [], 0
        now = int(time.time())
        out: list[LeaderSignal] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            if self.config.leader_require_buy_side and str(row.get("side", "")).upper() != "BUY":
                continue
            token_id = str(row.get("asset", "")).strip()
            slug = str(row.get("slug", "")).strip()
            outcome = str(row.get("outcome", "")).strip()
            price = _to_float(row.get("price"))
            notional = _to_float(row.get("usdcSize"))
            ts = _to_int(row.get("timestamp"))
            age = now - ts
            if not token_id or not slug or not outcome:
                continue
            if price <= 0 or price >= 1:
                continue
            if notional < self.config.leader_min_notional_usd:
                continue
            if age < 0 or age > self.config.leader_max_signal_age_seconds:
                continue
            out.append(
                LeaderSignal(
                    token_id=token_id,
                    slug=slug,
                    outcome_name=outcome,
                    observed_price=price,
                    observed_notional_usd=notional,
                    observed_ts_unix=ts,
                )
            )
        return out, len(data)

    def _signal_to_opportunity(
        self,
        signal: LeaderSignal,
        market: ParsedMarket,
        last_trade_at: dict[str, float],
    ) -> tuple[Opportunity | None, str | None]:
        if not market.active or market.closed or not market.accepting_orders or not market.enable_orderbook:
            return None, "market_state"
        if market.liquidity < self.config.min_liquidity:
            return None, "liquidity"
        if signal.token_id not in market.token_ids:
            return None, "token_mismatch"
        last = last_trade_at.get(market.id)
        if last is not None and (time.time() - last) < self.config.market_cooldown_seconds:
            return None, "cooldown"

        quote = self.data_client.fetch_best_ask(signal.token_id)
        ask = quote.best_ask
        if ask is None or ask <= 0 or ask >= 1:
            return None, "quote"

        max_follow_price = signal.observed_price * (1.0 + self.config.leader_price_tolerance_bps / 10_000.0)
        if ask > max_follow_price:
            return None, "price_drift"

        # Convert signal strength into a directional expected edge.
        size_boost = min(0.03, signal.observed_notional_usd / 10_000.0)
        modeled_prob = min(0.985, max(ask + 0.002, signal.observed_price + self.config.leader_alpha + size_boost))
        edge = modeled_prob - ask
        if edge <= 0:
            return None, "non_positive_edge"

        max_shares_by_capital = self.config.max_capital_per_trade / ask
        bundle_shares = min(self.config.max_bundle_shares, max_shares_by_capital)
        if bundle_shares <= 0:
            return None, "bundle"

        expected_profit = edge * bundle_shares
        leg = MarketLeg(
            market_id=market.market_id,
            market_slug=market.slug,
            market_question=market.question,
            token_id=signal.token_id,
            outcome_name=signal.outcome_name,
            price=ask,
            liquidity=market.liquidity,
        )
        return Opportunity(
            group_key=f"leader:{market.market_id}:{signal.token_id}:{signal.observed_ts_unix}",
            legs=[leg],
            sum_ask=ask,
            edge=edge,
            bundle_shares=bundle_shares,
            bundle_cost=ask * bundle_shares,
            guaranteed_payout_usd=bundle_shares,
            expected_profit_usd=expected_profit,
            min_leg_liquidity=market.liquidity,
        ), None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
