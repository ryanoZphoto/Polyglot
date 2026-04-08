from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

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
    Issue I fix: removed the pointless connectivity-only API call that made a live HTTP request
    every cycle and always returned None anyway. The method now fast-paths to None unless
    a real market-to-odds-book mapping layer is implemented.
    """

    def __init__(self, config: BotConfig):
        self.config = config

    def signal_for_market_question(self, question: str) -> ExternalSignal | None:
        if not self.config.enable_external_price_check:
            return None
        if not self.config.odds_api_key:
            return None
        # A robust market-question → external-odds mapping requires event-specific lookup tables.
        # Until that mapping layer is built, always return None to avoid spurious adjustments.
        # When implementing: fetch https://api.the-odds-api.com/v4/sports/{sport}/odds and match
        # on team/player names extracted from `question`, then compute fair_probability from
        # American/decimal odds and set confidence based on match quality.
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
