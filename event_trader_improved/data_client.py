# Copy from event_trader/data_client.py
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import EVConfig

logger = logging.getLogger(__name__)


class EVDataClient:
    def __init__(self, config: EVConfig):
        self.config = config
        self.gamma_host = config.gamma_host
        self.clob_host = config.clob_host
        self.timeout = config.request_timeout_seconds
        self.max_retries = config.max_request_retries
        self.backoff = config.retry_backoff_seconds

    def fetch_active_markets(self) -> list[dict]:
        """Fetch all active markets from Gamma API."""
        logger.info("Fetching active markets from Gamma API...")
        
        all_markets = []
        next_cursor = None
        page = 0
        
        while True:
            page += 1
            params = {"limit": 100, "active": "true"}
            if next_cursor:
                params["cursor"] = next_cursor
        
            try:
                resp = self.session.get(
                    f"{self.gamma_host}/markets",
                    params=params,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                
                markets = data.get("data", [])
                all_markets.extend(markets)
                
                next_cursor = data.get("next_cursor")
                logger.debug("Page %d: fetched %d markets, cursor=%s", page, len(markets), next_cursor)
                
                if not next_cursor or page >= 50:  # Safety limit
                    break
                
            except Exception as e:
                logger.error("Failed to fetch markets page %d: %s", page, e)
                break
        
        logger.info("Fetched %d total active markets across %d pages", len(all_markets), page)
        return all_markets

    def fetch_order_book(self, token_id: str) -> dict[str, Any]:
        url = f"{self.clob_host}/book"
        params = {"token_id": token_id}
        return self._get(url, params)

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        for attempt in range(self.max_retries):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error("request failed after %d retries: %s", self.max_retries, e)
                    raise
                time.sleep(self.backoff * (attempt + 1))
        return {}


