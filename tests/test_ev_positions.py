"""Tests for position management and exit logic."""
from __future__ import annotations

import os

os.environ.setdefault("EV_MODE", "dry_run")

from event_trader.config import EVConfig
from event_trader.positions import PositionManager
from event_trader.state import EVStateStore
from event_trader.types import BookLevel, OrderBook


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
        max_contracts_per_entry=500, max_positions=20,
        take_profit_pct=0.50, stop_loss_pct=0.40, trailing_stop_pct=0.20,
        longshot_take_profit_pct=1.50, longshot_stop_loss_pct=0.50,
        highprob_take_profit_pct=0.30, highprob_stop_loss_pct=0.20,
        max_total_exposure_usd=100, max_daily_loss_usd=25, max_per_market_usd=20,
        emergency_stop=False,
        request_timeout_seconds=8, max_request_retries=2, retry_backoff_seconds=0.3,
        max_workers=2,
        state_db_path=":memory:", log_path="test.log", report_path="test.jsonl",
    )
    defaults.update(overrides)
    return EVConfig(**defaults)


def _make_book(token_id: str, bid: float, ask: float) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=[BookLevel(bid, 100)],
        asks=[BookLevel(ask, 100)],
        fetched_at=0,
    )


def _open_test_position(state: EVStateStore, token_id: str = "tok1",
                        entry: float = 0.10, contracts: float = 50,
                        target: float = 0.15, stop: float = 0.06):
    state.open_position(
        "p1", "m1", token_id, "Yes", "Will X happen?",
        entry, contracts, entry * contracts, target, stop, "dry_run",
    )


class TestTakeProfit:
    def test_triggers_at_target(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10)  # target=0.15, stop=0.06
        mgr = PositionManager(config, state)

        books = {"tok1": _make_book("tok1", bid=0.16, ask=0.17)}
        exits = mgr.check_exits(books)

        assert len(exits) == 1
        assert exits[0]["reason"] == "take_profit"

    def test_no_trigger_below_target(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10)  # target=0.15
        mgr = PositionManager(config, state)

        books = {"tok1": _make_book("tok1", bid=0.12, ask=0.13)}
        exits = mgr.check_exits(books)

        assert len(exits) == 0


class TestStopLoss:
    def test_triggers_at_stop(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10)  # stop=0.06
        mgr = PositionManager(config, state)

        books = {"tok1": _make_book("tok1", bid=0.05, ask=0.06)}
        exits = mgr.check_exits(books)

        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"

    def test_no_trigger_above_stop(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10)  # stop=0.06
        mgr = PositionManager(config, state)

        books = {"tok1": _make_book("tok1", bid=0.08, ask=0.09)}
        exits = mgr.check_exits(books)

        assert len(exits) == 0


class TestTrailingStop:
    def test_trailing_stop_from_high_water(self):
        config = _make_config(trailing_stop_pct=0.20)
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10, target=0.90, stop=0.02)

        state.update_position_price("p1", 0.20)

        mgr = PositionManager(config, state)
        books = {"tok1": _make_book("tok1", bid=0.15, ask=0.16)}
        exits = mgr.check_exits(books)

        assert len(exits) == 1
        assert exits[0]["reason"] == "trailing_stop"

    def test_no_trailing_stop_when_disabled(self):
        config = _make_config(trailing_stop_pct=0.0)
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10, target=0.90, stop=0.02)
        state.update_position_price("p1", 0.20)

        mgr = PositionManager(config, state)
        books = {"tok1": _make_book("tok1", bid=0.15, ask=0.16)}
        exits = mgr.check_exits(books)

        assert len(exits) == 0


class TestPortfolioSummary:
    def test_empty_portfolio(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        mgr = PositionManager(config, state)

        summary = mgr.get_portfolio_summary({})
        assert summary["open_positions"] == 0
        assert summary["total_unrealized_pnl"] == 0

    def test_portfolio_with_position(self):
        config = _make_config()
        state = EVStateStore(":memory:")
        _open_test_position(state, entry=0.10, contracts=50)
        mgr = PositionManager(config, state)

        books = {"tok1": _make_book("tok1", bid=0.12, ask=0.13)}
        summary = mgr.get_portfolio_summary(books)

        assert summary["open_positions"] == 1
        assert summary["total_cost_usd"] == 5.0
        assert summary["total_value_usd"] == 6.0
        assert summary["total_unrealized_pnl"] == 1.0
