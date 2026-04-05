from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import BotConfig
from .polymarket import parse_market
from .types import ParsedMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BestAskQuote:
    token_id: str
    best_ask: float | None
    fetched_at_unix: float
    latency_ms: float


class ResilientDataClient:
    def __init__(self, config: BotConfig):
        self.config = config
        self.session = requests.Session()

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_request_retries + 1):
            started = time.time()
            try:
                resp = self.session.get(url, params=params, timeout=self.config.request_timeout_seconds)
                # Retry transient transport failures and server throttling.
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"transient_status={resp.status_code}", response=resp)
                resp.raise_for_status()
                return resp.json(), (time.time() - started) * 1000.0
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_request_retries:
                    break
                backoff = self.config.retry_backoff_seconds * (attempt + 1)
                time.sleep(backoff)
        if last_error is None:
            raise RuntimeError("Request failed without an exception")
        raise last_error

    def fetch_active_markets(self, limit: int) -> list[ParsedMarket]:
        payload, _latency_ms = self._request_json(
            f"{self.config.gamma_host}/markets",
            params={"active": "true", "closed": "false", "limit": str(limit)},
        )
        if not isinstance(payload, list):
            return []

        markets: list[ParsedMarket] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            parsed = parse_market(item)
            if parsed is not None:
                markets.append(parsed)
        return markets

    def fetch_best_ask(self, token_id: str) -> BestAskQuote:
        payload, latency_ms = self._request_json(
            f"{self.config.clob_host}/book",
            params={"token_id": token_id},
        )
        best_ask: float | None = None
        if isinstance(payload, dict):
            asks = payload.get("asks", [])
            if isinstance(asks, list) and asks:
                first = asks[0]
                if isinstance(first, dict):
                    try:
                        value = float(first.get("price"))
                        if 0.0 < value < 1.0:
                            best_ask = value
                    except (TypeError, ValueError):
                        best_ask = None

        if latency_ms > self.config.max_quote_fetch_latency_ms:
            logger.debug(
                "Ignoring stale best-ask token_id=%s latency_ms=%.2f threshold=%.2f",
                token_id,
                latency_ms,
                self.config.max_quote_fetch_latency_ms,
            )
            best_ask = None

        return BestAskQuote(
            token_id=token_id,
            best_ask=best_ask,
            fetched_at_unix=time.time(),
            latency_ms=latency_ms,
        )
