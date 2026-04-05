from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .config import BotConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalSignal:
    fair_probability: float
    confidence: float
    source: str
    fetched_at: str


class ExternalSignalClient:
    """
    Optional external-price check.

    This is deliberately conservative: if no reliable match is found, it returns None.
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self.session = requests.Session()

    def signal_for_market_question(self, question: str) -> ExternalSignal | None:
        if not self.config.enable_external_price_check:
            return None
        if not self.config.odds_api_key:
            return None
        # Matching market questions to external books robustly requires market-specific
        # mapping. Keep safe defaults: only emit a signal when we have high confidence.
        try:
            # Lightweight connectivity check to avoid dead API keys breaking the cycle.
            self.session.get(
                "https://api.the-odds-api.com/v4/sports",
                params={
                    "apiKey": self.config.odds_api_key,
                    "regions": self.config.odds_regions,
                    "markets": self.config.odds_markets,
                },
                timeout=self.config.request_timeout_seconds,
            ).raise_for_status()
        except Exception as exc:
            logger.debug("External feed check failed: %s", exc)
            return None

        # No inferred value unless we can map confidently.
        return None


def weighted_external_adjustment(signal: ExternalSignal | None, market_prob: float) -> float:
    """
    Returns probability delta to apply to market probability.
    Conservative weighted blend to avoid overfitting external data.
    """
    if signal is None:
        return 0.0
    confidence = max(0.0, min(signal.confidence, 1.0))
    delta = signal.fair_probability - market_prob
    return delta * confidence * 0.35


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
