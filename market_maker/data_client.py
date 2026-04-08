from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from .config import MMConfig
from .types import BookLevel, OrderBook, ParsedMarket

logger = logging.getLogger(__name__)


class MMDataClient:
    def __init__(self, config: MMConfig):
        self.config = config
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=max(10, config.max_workers),
            pool_maxsize=max(20, config.max_workers * 2),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_request_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.config.request_timeout_seconds)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"transient_status={resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_request_retries:
                    break
                time.sleep(self.config.retry_backoff_seconds * (attempt + 1))
        raise last_error or RuntimeError("Request failed")

    def fetch_active_markets(self, limit: int | None = None) -> list[ParsedMarket]:
        lim = limit or self.config.scan_limit
        payload = self._request_json(
            f"{self.config.gamma_host}/markets",
            params={"active": "true", "closed": "false", "limit": str(lim)},
        )
        if not isinstance(payload, list):
            return []
        markets: list[ParsedMarket] = []
        for item in payload:
            parsed = self._parse_market(item)
            if parsed is not None:
                markets.append(parsed)
        return markets

    def fetch_order_book(self, token_id: str) -> OrderBook:
        payload = self._request_json(
            f"{self.config.clob_host}/book",
            params={"token_id": token_id},
        )
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []
        if isinstance(payload, dict):
            for entry in payload.get("bids", []):
                if isinstance(entry, dict):
                    try:
                        bids.append(BookLevel(float(entry["price"]), float(entry["size"])))
                    except (KeyError, TypeError, ValueError):
                        continue
            for entry in payload.get("asks", []):
                if isinstance(entry, dict):
                    try:
                        asks.append(BookLevel(float(entry["price"]), float(entry["size"])))
                    except (KeyError, TypeError, ValueError):
                        continue
        return OrderBook(token_id=token_id, bids=bids, asks=asks, fetched_at=time.time())

    @staticmethod
    def _parse_market(raw: dict[str, Any]) -> ParsedMarket | None:
        market_id = str(raw.get("id", "")).strip()
        question = str(raw.get("question", "")).strip()
        slug = str(raw.get("slug", "")).strip()
        if not market_id or not question or not slug:
            return None

        def parse_list(val: Any) -> list[str]:
            if isinstance(val, list):
                return [str(x) for x in val]
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return [str(x) for x in parsed] if isinstance(parsed, list) else []
                except json.JSONDecodeError:
                    return []
            return []

        outcomes = parse_list(raw.get("outcomes"))
        token_ids = parse_list(raw.get("clobTokenIds"))
        if not outcomes or not token_ids or len(outcomes) != len(token_ids):
            return None

        liquidity = 0.0
        try:
            liquidity = float(raw.get("liquidityNum", raw.get("liquidity", 0)))
        except (TypeError, ValueError):
            pass

        def as_bool(val: Any, default: bool) -> bool:
            if isinstance(val, bool):
                return val
            if val is None:
                return default
            if isinstance(val, str):
                return val.strip().lower() in {"1", "true", "yes", "on"}
            return bool(val)

        events = raw.get("events", [])
        event_title = event_slug = None
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event_title = str(events[0].get("title", "")).strip() or None
            event_slug = str(events[0].get("slug", "")).strip() or None

        return ParsedMarket(
            market_id=market_id,
            question=question,
            slug=slug,
            liquidity=liquidity,
            active=bool(raw.get("active", False)),
            closed=bool(raw.get("closed", True)),
            accepting_orders=bool(raw.get("acceptingOrders", False)),
            enable_orderbook=as_bool(raw.get("enableOrderBook", raw.get("enable_orderbook")), True),
            neg_risk=as_bool(raw.get("negRisk", raw.get("neg_risk")), False),
            outcomes=outcomes,
            token_ids=token_ids,
            event_title=event_title,
            event_slug=event_slug,
        )
