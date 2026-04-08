"""Tests for market_maker.quoting and market_maker.fair_value."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("MM_MODE", "dry_run")

from market_maker.config import MMConfig
from market_maker.fair_value import book_imbalance, estimate_fair_value
from market_maker.inventory import InventoryManager
from market_maker.quoting import QuotingEngine
from market_maker.state import MMStateStore
from market_maker.types import BookLevel, OrderBook


def _book(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBook:
    return OrderBook(
        token_id="test_token",
        bids=[BookLevel(p, s) for p, s in bids],
        asks=[BookLevel(p, s) for p, s in asks],
        fetched_at=0,
    )


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


class TestFairValue:
    def test_simple_mid(self):
        book = _book([(0.45, 100)], [(0.55, 100)])
        fv = estimate_fair_value(book)
        assert fv == pytest.approx(0.50, abs=0.001)

    def test_depth_weighted_toward_bids(self):
        book = _book([(0.45, 500), (0.44, 300)], [(0.55, 50)])
        fv = estimate_fair_value(book)
        assert fv is not None
        assert fv < 0.55

    def test_empty_bids_returns_none(self):
        book = _book([], [(0.55, 100)])
        fv = estimate_fair_value(book)
        assert fv is None

    def test_empty_asks_returns_none(self):
        book = _book([(0.45, 100)], [])
        fv = estimate_fair_value(book)
        assert fv is None

    def test_clamped_to_book_range(self):
        book = _book([(0.50, 1000)], [(0.51, 10)])
        fv = estimate_fair_value(book)
        assert fv is not None
        assert 0.50 <= fv <= 0.51


class TestBookImbalance:
    def test_balanced(self):
        book = _book([(0.50, 100)], [(0.51, 100)])
        assert book_imbalance(book) == pytest.approx(0.0, abs=0.01)

    def test_bid_heavy(self):
        book = _book([(0.50, 300)], [(0.51, 100)])
        imb = book_imbalance(book)
        assert imb > 0

    def test_ask_heavy(self):
        book = _book([(0.50, 100)], [(0.51, 300)])
        imb = book_imbalance(book)
        assert imb < 0


class TestQuotingEngine:
    def _make_engine(self, **config_overrides):
        cfg = _config(**config_overrides)
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name
        cfg = _config(state_db_path=db_path, **config_overrides)
        state = MMStateStore(db_path)
        inv = InventoryManager(cfg, state)
        engine = QuotingEngine(cfg, inv)
        return engine, state, db_path

    def test_generates_two_sided_quote(self):
        engine, state, _ = self._make_engine(half_spread=0.02)
        book = _book([(0.48, 100), (0.47, 50)], [(0.52, 100), (0.53, 50)])
        q = engine.generate_quote("mkt1", "tok1", "Yes", book)
        assert q is not None
        assert q.bid_price < q.ask_price
        assert q.bid_size > 0
        assert q.ask_size > 0
        assert q.spread > 0

    def test_returns_none_for_narrow_spread(self):
        engine, state, _ = self._make_engine(min_book_spread=0.05)
        book = _book([(0.49, 100)], [(0.50, 100)])
        q = engine.generate_quote("mkt1", "tok1", "Yes", book)
        assert q is None

    def test_returns_none_for_empty_book(self):
        engine, state, _ = self._make_engine()
        book = _book([], [])
        q = engine.generate_quote("mkt1", "tok1", "Yes", book)
        assert q is None

    def test_skew_applied_with_inventory(self):
        engine, state, _ = self._make_engine(half_spread=0.03, max_inventory_per_market=100)
        state.update_inventory("tok1", "mkt1", "Yes", 50.0, 0.50)
        book = _book([(0.48, 100)], [(0.52, 100)])
        q = engine.generate_quote("mkt1", "tok1", "Yes", book)
        assert q is not None
        assert q.skew_applied != 0.0
