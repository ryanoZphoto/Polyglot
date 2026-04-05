from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

import requests

from .types import OutcomeQuote, ParsedMarket


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_title(raw: Dict[str, Any]) -> str | None:
    events = raw.get("events", [])
    if not isinstance(events, list) or not events:
        return None
    first = events[0]
    if not isinstance(first, dict):
        return None
    title = first.get("title")
    if title is None:
        return None
    text = str(title).strip()
    return text or None


def _event_slug(raw: Dict[str, Any]) -> str | None:
    events = raw.get("events", [])
    if not isinstance(events, list) or not events:
        return None
    first = events[0]
    if not isinstance(first, dict):
        return None
    slug = first.get("slug")
    if slug is None:
        return None
    text = str(slug).strip()
    return text or None


def _is_sports_market(question: str, slug: str, event_title: str | None) -> bool:
    lower_text = f"{question} {slug} {event_title or ''}".lower()
    return any(
        k in lower_text
        for k in (
            " vs ",
            " mlb",
            " nba",
            " nfl",
            " nhl",
            " ncaa",
            " college ",
            "atp",
            "wta",
            "premier league",
            "champions league",
            "f1",
            "formula 1",
            "ufc",
            "mma",
            "soccer",
            "tennis",
            "basketball",
            "baseball",
            "golf",
            "boxing",
        )
    )


def parse_market(raw: Dict[str, Any]) -> ParsedMarket | None:
    market_id = str(raw.get("id", "")).strip()
    question = str(raw.get("question", "")).strip()
    slug = str(raw.get("slug", "")).strip()
    liquidity = _as_float(raw.get("liquidityNum", raw.get("liquidity", 0.0)))

    outcomes = [str(x) for x in _parse_json_list(raw.get("outcomes"))]
    token_ids = [str(x) for x in _parse_json_list(raw.get("clobTokenIds"))]
    if not market_id or not question or not slug:
        return None
    if not outcomes or not token_ids:
        return None
    if len(outcomes) != len(token_ids):
        return None

    event_title = _event_title(raw)
    event_slug = _event_slug(raw)
    group_item_title = str(raw.get("groupItemTitle", "")).strip() or None
    is_sports = _is_sports_market(question, slug, event_title)

    return ParsedMarket(
        market_id=market_id,
        question=question,
        slug=slug,
        liquidity=liquidity,
        active=bool(raw.get("active", False)),
        closed=bool(raw.get("closed", True)),
        accepting_orders=bool(raw.get("acceptingOrders", False)),
        outcomes=outcomes,
        token_ids=token_ids,
        event_title=event_title,
        event_slug=event_slug,
        group_item_title=group_item_title,
        best_asks=[OutcomeQuote(name=o, token_id=t, best_ask=None) for o, t in zip(outcomes, token_ids)],
        is_sports=is_sports,
    )


@dataclass
class PolymarketDataClient:
    gamma_host: str
    clob_host: str
    timeout_seconds: float = 10.0

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = requests.get(url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def fetch_markets(self, limit: int) -> List[ParsedMarket]:
        return self.fetch_active_markets(limit)

    def fetch_active_markets(self, limit: int) -> List[ParsedMarket]:
        response = requests.get(
            f"{self.gamma_host}/markets",
            params={"active": "true", "closed": "false", "limit": str(limit)},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        markets: List[ParsedMarket] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            market = parse_market(item)
            if market is None:
                continue
            markets.append(market)
        return markets

    def get_best_ask(self, token_id: str) -> float | None:
        response = requests.get(
            f"{self.clob_host}/book",
            params={"token_id": token_id},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        asks = payload.get("asks", [])
        if not asks or not isinstance(asks, list):
            return None
        first = asks[0]
        if not isinstance(first, dict):
            return None
        price = _as_float(first.get("price"), default=float("nan"))
        if price != price:
            return None
        return price

    def fetch_best_ask(self, token_id: str) -> float | None:
        return self.get_best_ask(token_id)


# Backwards-compatible alias used by existing bot imports.
PolymarketClient = PolymarketDataClient


def extract_no_quote(market: ParsedMarket) -> OutcomeQuote | None:
    """Return the NO quote for two-outcome markets, if identifiable."""
    if len(market.outcomes) != 2:
        return None
    for quote in market.best_asks:
        if quote.name.strip().lower() == "no":
            return quote
    return None
