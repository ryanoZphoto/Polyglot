from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from .config import EVConfig
from .types import BookLevel, OrderBook, ParsedMarket

logger = logging.getLogger(__name__)


class EVDataClient:
    def __init__(self, config: EVConfig):
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
                resp = self.session.get(
                    url, params=params, timeout=self.config.request_timeout_seconds,
                )
                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"transient_status={resp.status_code}", response=resp,
                    )
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_request_retries:
                    break
                time.sleep(self.config.retry_backoff_seconds * (attempt + 1))
        raise last_error or RuntimeError("Request failed")

    def fetch_active_markets(self, limit: int | None = None) -> list[ParsedMarket]:
        """Fetch markets across multiple pages to find cheap contracts deeper in the list."""
        lim = limit or self.config.scan_limit
        all_markets: list[ParsedMarket] = []
        seen_ids: set[str] = set()
        page_size = min(100, lim)
        offset = 0

        while len(all_markets) < lim:
            try:
                payload = self._request_json(
                    f"{self.config.gamma_host}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": str(page_size),
                        "offset": str(offset),
                    },
                )
            except Exception:
                logger.warning("market fetch failed at offset %d", offset)
                break

            if not isinstance(payload, list) or not payload:
                break

            for item in payload:
                parsed = self._parse_market(item)
                if parsed is not None and parsed.market_id not in seen_ids:
                    seen_ids.add(parsed.market_id)
                    all_markets.append(parsed)

            if len(payload) < page_size:
                break
            offset += page_size

        logger.info("fetched %d markets across %d pages", len(all_markets), (offset // page_size) + 1)
        return all_markets

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

    def fetch_live_prices(self, token_ids: list[str]) -> dict[str, float]:
        """Fetch the best available live price for each token.

        Priority:
        1. Last trade price -- the most recent actual transaction.
           On Polymarket most books are extremely wide ($0.001-$0.999)
           so the midpoint is meaningless, but last-trade reflects where
           real money actually changed hands.
        2. CLOB midpoint -- only used when spread is tight (<$0.10),
           meaning the book has real two-sided liquidity.
        3. Gamma outcomePrices -- caller's fallback (not handled here).
        """
        if not token_ids:
            return {}
        results: dict[str, float] = {}
        batch_size = 50

        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i : i + batch_size]
            try:
                ltp_payload = self.session.post(
                    f"{self.config.clob_host}/last-trade-price",
                    json=[{"token_id": tid} for tid in batch],
                    timeout=self.config.request_timeout_seconds,
                ).json()
                if isinstance(ltp_payload, dict):
                    for tid, val in ltp_payload.items():
                        try:
                            p = float(val) if not isinstance(val, dict) else float(val.get("price", 0))
                            if p > 0:
                                results[tid] = p
                        except (TypeError, ValueError):
                            pass
            except Exception:
                logger.debug("last-trade-price batch failed for %d tokens", len(batch))

        missing = [tid for tid in token_ids if tid not in results]
        if missing:
            for i in range(0, len(missing), batch_size):
                batch = missing[i : i + batch_size]
                try:
                    spread_payload = self.session.post(
                        f"{self.config.clob_host}/spreads",
                        json=[{"token_id": tid} for tid in batch],
                        timeout=self.config.request_timeout_seconds,
                    ).json()
                    tight_tids = set()
                    if isinstance(spread_payload, dict):
                        for tid, val in spread_payload.items():
                            try:
                                spread = float(val)
                                if spread < 0.10:
                                    tight_tids.add(tid)
                            except (TypeError, ValueError):
                                pass

                    if tight_tids:
                        mid_payload = self.session.post(
                            f"{self.config.clob_host}/midpoints",
                            json=[{"token_id": tid} for tid in tight_tids],
                            timeout=self.config.request_timeout_seconds,
                        ).json()
                        if isinstance(mid_payload, dict):
                            for tid, val in mid_payload.items():
                                try:
                                    mid = float(val)
                                    if mid > 0:
                                        results[tid] = mid
                                except (TypeError, ValueError):
                                    pass
                except Exception:
                    logger.debug("midpoint/spread fallback failed for %d tokens", len(batch))

        return results

    def fetch_price_history(self, token_id: str, fidelity: int = 60) -> list[dict]:
        """Fetch recent price/time series for a token. fidelity is in minutes."""
        try:
            payload = self._request_json(
                f"{self.config.clob_host}/prices-history",
                params={"tokenID": token_id, "fidelity": str(fidelity)},
            )
            if isinstance(payload, dict):
                history = payload.get("history", [])
                return history if isinstance(history, list) else []
            return payload if isinstance(payload, list) else []
        except Exception:
            logger.debug("price history unavailable for %s", token_id[:16])
            return []

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

        volume = 0.0
        try:
            volume = float(raw.get("volumeNum", raw.get("volume", 0)))
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

        outcome_prices_raw = parse_list(raw.get("outcomePrices"))
        outcome_prices: list[float] = []
        for p in outcome_prices_raw:
            try:
                outcome_prices.append(float(p))
            except (ValueError, TypeError):
                outcome_prices.append(0.0)

        return ParsedMarket(
            market_id=market_id,
            question=question,
            slug=slug,
            liquidity=liquidity,
            volume=volume,
            active=bool(raw.get("active", False)),
            closed=bool(raw.get("closed", True)),
            accepting_orders=bool(raw.get("acceptingOrders", False)),
            enable_orderbook=as_bool(raw.get("enableOrderBook", raw.get("enable_orderbook")), True),
            neg_risk=as_bool(raw.get("negRisk", raw.get("neg_risk")), False),
            outcomes=outcomes,
            token_ids=token_ids,
            outcome_prices=outcome_prices,
            event_title=event_title,
            event_slug=event_slug,
            end_date=str(raw.get("endDate", "")).strip() or None,
            description=str(raw.get("description", "")).strip() or None,
        )
