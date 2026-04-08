from __future__ import annotations

import json
import logging
import time
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


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


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
        enable_orderbook=_as_bool(raw.get("enableOrderBook", raw.get("enable_orderbook")), True),
        neg_risk=_as_bool(raw.get("negRisk", raw.get("neg_risk")), False),
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
    max_retries: int = 2
    retry_backoff_seconds: float = 0.35

    def __post_init__(self) -> None:
        # Use a session to enable connection pooling, significantly reducing latency 
        # for sequential requests (Issue #2).
        self._session = requests.Session()
        # Increase pool size to support high concurrency in the parallel scanner.
        adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.get(url, params=params, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                if attempt >= self.max_retries:
                    logging.error(f"Request failed after {self.max_retries} retries: {e}")
                    raise
                time.sleep(self.retry_backoff_seconds * (attempt + 1))

    def fetch_markets(self, limit: int) -> List[ParsedMarket]:
        return self.fetch_active_markets(limit)

    def fetch_active_markets(self, limit: int) -> List[ParsedMarket]:
        payload = self._get(
            f"{self.gamma_host}/markets",
            params={"active": "true", "closed": "false", "limit": str(limit)},
        )
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
        payload = self._get(
            f"{self.clob_host}/book",
            params={"token_id": token_id},
        )
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
