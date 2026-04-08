"""Tests for market_maker.inventory."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("MM_MODE", "dry_run")

from market_maker.config import MMConfig
from market_maker.inventory import InventoryManager
from market_maker.state import MMStateStore


def _config(**overrides) -> MMConfig:
    defaults = {
        "gamma_host": "https://gamma-api.polymarket.com",
        "clob_host": "https://clob.polymarket.com",
        "chain_id": 137,
        "private_key": None,
        "funder": None,
        "signature_type": 0,
        "api_key": None,
        "api_secret": None,
        "api_passphrase": None,
        "mode": "dry_run",
        "dry_run": True,
        "poll_interval_seconds": 5.0,
        "log_json": False,
        "markets": ["auto"],
        "min_liquidity": 5000,
        "scan_limit": 200,
        "half_spread": 0.02,
        "quote_size": 20.0,
        "min_book_spread": 0.01,
        "max_inventory_per_market": 100.0,
        "skew_factor": 0.5,
        "max_total_exposure_usd": 500.0,
        "max_open_orders": 40,
        "max_daily_loss_usd": 50.0,
        "max_orders_per_minute": 30,
        "emergency_stop": False,
        "request_timeout_seconds": 8,
        "max_request_retries": 2,
        "retry_backoff_seconds": 0.3,
        "max_workers": 4,
        "state_db_path": ":memory:",
        "log_path": "test.log",
        "report_path": "test_report.jsonl",
    }
    defaults.update(overrides)
    return MMConfig(**defaults)


@pytest.fixture
def manager():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        db_path = tmp.name
    config = _config(state_db_path=db_path)
    state = MMStateStore(db_path)
    mgr = InventoryManager(config, state)
    yield mgr
    state.close()


class TestInventoryManager:
    def test_empty_position(self, manager):
        pos = manager.get_position("tok1", "mkt1", "Yes")
        assert pos.position_shares == 0.0
        assert pos.side == "flat"

    def test_buy_creates_long(self, manager):
        manager.record_fill("tok1", "mkt1", "Yes", "BUY", 0.50, 25.0)
        pos = manager.get_position("tok1", "mkt1", "Yes")
        assert pos.position_shares == pytest.approx(25.0)
        assert pos.side == "long"

    def test_sell_reduces_position(self, manager):
        manager.record_fill("tok1", "mkt1", "Yes", "BUY", 0.50, 30.0)
        manager.record_fill("tok1", "mkt1", "Yes", "SELL", 0.55, 10.0)
        pos = manager.get_position("tok1", "mkt1", "Yes")
        assert pos.position_shares == pytest.approx(20.0)

    def test_sell_past_zero_goes_short(self, manager):
        manager.record_fill("tok1", "mkt1", "Yes", "BUY", 0.50, 10.0)
        manager.record_fill("tok1", "mkt1", "Yes", "SELL", 0.55, 20.0)
        pos = manager.get_position("tok1", "mkt1", "Yes")
        assert pos.position_shares == pytest.approx(-10.0)
        assert pos.side == "short"


class TestSkew:
    def test_no_skew_for_flat(self, manager):
        skew = manager.compute_skew(0.0)
        assert skew == 0.0

    def test_negative_skew_for_long(self, manager):
        skew = manager.compute_skew(50.0)
        assert skew < 0

    def test_positive_skew_for_short(self, manager):
        skew = manager.compute_skew(-50.0)
        assert skew > 0

    def test_at_limit(self, manager):
        manager.record_fill("tok1", "mkt1", "Yes", "BUY", 0.50, 100.0)
        assert manager.is_at_limit("tok1") is True

    def test_not_at_limit(self, manager):
        manager.record_fill("tok1", "mkt1", "Yes", "BUY", 0.50, 50.0)
        assert manager.is_at_limit("tok1") is False
