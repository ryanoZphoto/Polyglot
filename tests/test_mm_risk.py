"""Tests for market_maker.risk."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("MM_MODE", "dry_run")

from market_maker.config import MMConfig
from market_maker.risk import MMRiskEngine
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
def risk_engine():
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
        db_path = tmp.name
    config = _config(state_db_path=db_path)
    state = MMStateStore(db_path)
    engine = MMRiskEngine(config, state)
    yield engine, config, state
    state.close()


class TestPreCycleCheck:
    def test_passes_normally(self, risk_engine):
        engine, _, _ = risk_engine
        result = engine.pre_cycle_check()
        assert result.allowed is True

    def test_blocked_by_emergency_stop(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name
        config = _config(state_db_path=db_path, emergency_stop=True)
        state = MMStateStore(db_path)
        engine = MMRiskEngine(config, state)
        result = engine.pre_cycle_check()
        assert result.allowed is False
        assert "emergency" in result.reason.lower()
        state.close()

    def test_blocked_by_daily_loss(self, risk_engine):
        engine, _, state = risk_engine
        state.record_fill("f1", "o1", "mkt1", "tok1", "SELL", 0.40, 100, 0, 0, -60.0, "dry_run")
        result = engine.pre_cycle_check()
        assert result.allowed is False
        assert "daily loss" in result.reason.lower()


class TestPreOrderCheck:
    def test_passes_normally(self, risk_engine):
        engine, _, _ = risk_engine
        result = engine.pre_order_check("mkt1", "tok1", 10.0)
        assert result.allowed is True

    def test_blocked_by_emergency_stop_per_order(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name
        config = _config(state_db_path=db_path, emergency_stop=True)
        state = MMStateStore(db_path)
        engine = MMRiskEngine(config, state)
        result = engine.pre_order_check("m1", "t1", 5.0)
        assert result.allowed is False
        assert "emergency" in result.reason.lower()
        state.close()

    def test_blocked_by_exposure_at_cycle_level(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name
        config = _config(state_db_path=db_path, max_total_exposure_usd=50.0)
        state = MMStateStore(db_path)
        engine = MMRiskEngine(config, state)
        state.update_inventory("tok1", "mkt1", "Yes", 200.0, 0.50)
        result = engine.pre_cycle_check()
        assert result.allowed is False
        assert "exposure" in result.reason.lower()
        state.close()

    def test_blocked_by_inventory_limit(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name
        config = _config(state_db_path=db_path, max_inventory_per_market=50.0)
        state = MMStateStore(db_path)
        engine = MMRiskEngine(config, state)
        state.update_inventory("tok1", "mkt1", "Yes", 55.0, 0.50)
        result = engine.pre_order_check("mkt1", "tok1", 5.0)
        assert result.allowed is False
        assert "inventory" in result.reason.lower()
        state.close()
