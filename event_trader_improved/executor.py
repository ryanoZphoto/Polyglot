# Copy from event_trader/executor.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .config import EVConfig
from .state import EVStateStore

logger = logging.getLogger(__name__)


@dataclass
class EVSignal:
    token_id: str
    market_slug: str
    outcome: str
    entry_price: float
    shares: float
    stop_loss: float
    take_profit: float
    edge: float
    reason: str


class BaseEVExecutor(ABC):
    @abstractmethod
    def execute_entry(self, signal: EVSignal, cycle_id: str) -> bool:
        pass

    @abstractmethod
    def execute_exit(self, token_id: str, price: float, shares: float, reason: str, cycle_id: str) -> bool:
        pass


class DryRunEVExecutor(BaseEVExecutor):
    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state

    def execute_entry(self, signal: EVSignal, cycle_id: str) -> bool:
        logger.info("[DRY] ENTRY %s @ %.4f (%d shares) edge=%.2f%% reason=%s",
                    signal.outcome, signal.entry_price, int(signal.shares), signal.edge * 100, signal.reason)
        return True

    def execute_exit(self, token_id: str, price: float, shares: float, reason: str, cycle_id: str) -> bool:
        logger.info("[DRY] EXIT %s @ %.4f (%d shares) reason=%s", token_id[:16], price, int(shares), reason)
        return True


class LiveEVExecutor(BaseEVExecutor):
    def __init__(self, config: EVConfig, state: EVStateStore):
        self.config = config
        self.state = state
        # TODO: Initialize py-clob-client here

    def execute_entry(self, signal: EVSignal, cycle_id: str) -> bool:
        logger.info("[LIVE] ENTRY %s @ %.4f (%d shares)", signal.outcome, signal.entry_price, int(signal.shares))
        # TODO: Place actual order via CLOB
        return False

    def execute_exit(self, token_id: str, price: float, shares: float, reason: str, cycle_id: str) -> bool:
        logger.info("[LIVE] EXIT %s @ %.4f (%d shares)", token_id[:16], price, int(shares))
        # TODO: Place actual order via CLOB
        return False