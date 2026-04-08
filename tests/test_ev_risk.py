"""Tests for event trader risk engine."""
from __future__ import annotations

import os

os.environ.setdefault("EV_MODE", "dry_run")

from event_trader.config import EVConfig
from event_trader.risk import EVRiskEngine
from event_trader.state import EVStateStore


def _make_config(**overrides) -> EVConfig:
    defaults = dict(
        gamma_host="https://gamma-api.polymarket.com",
        clob_host="https://clob.polymarket.com",
        chain_id=137, private_key=None, funder=None, signature_type=0,
        api_key=None, api_secret=None, api_passphrase=None,
        mode="dry_run", dry_run=True, poll_interval_seconds=10, log_json=False,
        scan_limit=100, min_liquidity=1000, min_volume=500,
        max_entry_price=0.40, min_entry_price=0.02,
        min_book_depth_usd=50, min_spread_ratio=0.30,
        position_size_usd=5,
        longshot_size_usd=2, highprob_size_usd=8,
        longshot_ceiling=0.30, highprob_floor=0.55,
        max_contracts_per_entry=500, max_positions=3,
        take_profit_pct=0.50, stop_loss_pct=0.40, trailing_stop_pct=0,
        longshot_take_profit_pct=1.50, longshot_stop_loss_pct=0.50,
        highprob_take_profit_pct=0.30, highprob_stop_loss_pct=0.20,
        max_total_exposure_usd=50, max_daily_loss_usd=25, max_per_market_usd=20,
        emergency_stop=False,
        request_timeout_seconds=8, max_request_retries=2, retry_backoff_seconds=0.3,
        max_workers=2,
        state_db_path=":memory:", log_path="test.log", report_path="test.jsonl",
    )
    defaults.update(overrides)
    return EVConfig(**defaults)


class TestPreCycleCheck:
    def test_passes_normally(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        engine = EVRiskEngine(config, state)
        result = engine.pre_cycle_check()
        assert result.allowed is True

    def test_blocked_by_emergency_stop(self):
        config = _make_config(emergency_stop=True)
        state = EVStateStore(":memory:")
        engine = EVRiskEngine(config, state)
        result = engine.pre_cycle_check()
        assert result.allowed is False
        assert "emergency" in result.reason.lower()


class TestPreEntryCheck:
    def test_passes_normally(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        engine = EVRiskEngine(config, state)
        result = engine.pre_entry_check("m1", "tok1", 5.0)
        assert result.allowed is True

    def test_blocked_by_max_positions(self):
        config = _make_config(max_positions=2)
        state = EVStateStore(":memory:")
        state.open_position("p1", "m1", "t1", "Yes", "Q1?", 0.1, 50, 5, 0.15, 0.06, "dry_run")
        state.open_position("p2", "m2", "t2", "Yes", "Q2?", 0.1, 50, 5, 0.15, 0.06, "dry_run")

        engine = EVRiskEngine(config, state)
        result = engine.pre_entry_check("m3", "t3", 5.0)
        assert result.allowed is False
        assert "max positions" in result.reason.lower()

    def test_blocked_by_duplicate_token(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        state.open_position("p1", "m1", "tok1", "Yes", "Q?", 0.1, 50, 5, 0.15, 0.06, "dry_run")

        engine = EVRiskEngine(config, state)
        result = engine.pre_entry_check("m1", "tok1", 5.0)
        assert result.allowed is False
        assert "already have position" in result.reason.lower()

    def test_blocked_by_exposure_limit(self):
        config = _make_config(max_total_exposure_usd=10)
        state = EVStateStore(":memory:")
        state.open_position("p1", "m1", "t1", "Yes", "Q?", 0.1, 100, 10, 0.15, 0.06, "dry_run")

        engine = EVRiskEngine(config, state)
        result = engine.pre_entry_check("m2", "t2", 5.0)
        assert result.allowed is False
        assert "exposure" in result.reason.lower()

    def test_blocked_by_per_market_limit(self):
        config = _make_config(max_per_market_usd=10)
        state = EVStateStore(":memory:")
        state.open_position("p1", "m1", "t1", "Yes", "Q?", 0.1, 100, 10, 0.15, 0.06, "dry_run")

        engine = EVRiskEngine(config, state)
        result = engine.pre_entry_check("m1", "t2", 5.0)
        assert result.allowed is False
        assert "per-market" in result.reason.lower()
