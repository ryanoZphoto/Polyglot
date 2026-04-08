"""Tests for the event trader opportunity scanner."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("EV_MODE", "dry_run")

from event_trader.config import EVConfig
from event_trader.data_client import EVDataClient
from event_trader.scanner import OpportunityScanner
from event_trader.state import EVStateStore
from event_trader.types import BookLevel, OrderBook, ParsedMarket


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
        take_profit_pct=0.50, stop_loss_pct=0.40, trailing_stop_pct=0,
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


def _make_market(market_id: str = "m1", question: str = "Test?",
                 liquidity: float = 5000, volume: float = 10000,
                 prices: list[float] | None = None) -> ParsedMarket:
    return ParsedMarket(
        market_id=market_id, question=question, slug="test",
        liquidity=liquidity, volume=volume,
        active=True, closed=False, accepting_orders=True,
        enable_orderbook=True, neg_risk=False,
        outcomes=["Yes", "No"],
        token_ids=["tok_yes", "tok_no"],
        outcome_prices=prices if prices is not None else [0.10, 0.90],
        event_title=None, event_slug=None, end_date=None, description=None,
    )


def _make_book(token_id: str, bid: float, ask: float,
               bid_size: float = 200, ask_size: float = 200) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=[BookLevel(bid, bid_size)],
        asks=[BookLevel(ask, ask_size)],
        fetched_at=0,
    )


class TestScanner:
    def _setup(self, **config_overrides):
        self.config = _make_config(**config_overrides)
        self.state = EVStateStore(":memory:")
        self.client = EVDataClient(self.config)
        self.scanner = OpportunityScanner(self.config, self.client, self.state)

    def test_finds_cheap_contract(self):
        self._setup()
        market = _make_market()
        book_yes = _make_book("tok_yes", bid=0.09, ask=0.10, ask_size=600)
        book_no = _make_book("tok_no", bid=0.89, ask=0.91, ask_size=200)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book_yes, "tok_no": book_no}
        signals = self.scanner.scan([market])

        assert len(signals) == 1
        assert signals[0].token_id == "tok_yes"
        assert signals[0].entry_price == 0.10

    def test_skips_expensive_contract(self):
        self._setup(max_entry_price=0.30)
        market = _make_market(prices=[0.50, 0.50])
        book_yes = _make_book("tok_yes", bid=0.49, ask=0.50, ask_size=200)
        book_no = _make_book("tok_no", bid=0.49, ask=0.51, ask_size=200)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book_yes, "tok_no": book_no}
        signals = self.scanner.scan([market])

        assert len(signals) == 0

    def test_skips_dust_price(self):
        self._setup()
        market = _make_market(prices=[0.01, 0.99])
        book = _make_book("tok_yes", bid=0.005, ask=0.01, ask_size=200)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book, "tok_no": _make_book("tok_no", 0.99, 0.999)}
        signals = self.scanner.scan([market])

        assert all(s.entry_price >= 0.02 for s in signals)

    def test_skips_illiquid_book(self):
        self._setup(min_book_depth_usd=100)
        market = _make_market()
        book = _make_book("tok_yes", bid=0.09, ask=0.10, ask_size=5)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book, "tok_no": _make_book("tok_no", 0.89, 0.91)}
        signals = self.scanner.scan([market])

        yes_signals = [s for s in signals if s.token_id == "tok_yes"]
        assert len(yes_signals) == 0

    def test_skips_existing_position(self):
        self._setup()
        market = _make_market()
        book = _make_book("tok_yes", bid=0.09, ask=0.10, ask_size=600)

        self.state.open_position(
            "p1", "m1", "tok_yes", "Yes", "Test?",
            0.10, 50, 5.0, 0.15, 0.06, "dry_run",
        )

        self.scanner._fetch_books = lambda tids: {"tok_yes": book, "tok_no": _make_book("tok_no", 0.89, 0.91)}
        signals = self.scanner.scan([market])

        assert all(s.token_id != "tok_yes" for s in signals)

    def test_confidence_scoring(self):
        self._setup()
        market = _make_market(liquidity=50000, volume=100000)
        book = _make_book("tok_yes", bid=0.04, ask=0.05, ask_size=1000)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book, "tok_no": _make_book("tok_no", 0.94, 0.96)}
        signals = self.scanner.scan([market])

        yes_sigs = [s for s in signals if s.token_id == "tok_yes"]
        assert len(yes_sigs) == 1
        assert yes_sigs[0].confidence > 0.5

    def test_sets_target_and_stop_longshot(self):
        """$0.10 entry falls in longshot tier: tp=+150%, sl=-50%."""
        self._setup()
        market = _make_market()
        book = _make_book("tok_yes", bid=0.09, ask=0.10, ask_size=600)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book, "tok_no": _make_book("tok_no", 0.89, 0.91)}
        signals = self.scanner.scan([market])

        sig = [s for s in signals if s.token_id == "tok_yes"][0]
        assert sig.tier == "longshot"
        assert sig.target_price == 0.25  # 0.10 * (1 + 1.50)
        assert sig.stop_loss_price == 0.05  # 0.10 * (1 - 0.50)
        assert sig.sized_usd == 2.0

    def test_sets_target_and_stop_highprob(self):
        """$0.60 entry falls in highprob tier: tp=+30%, sl=-20%."""
        self._setup(max_entry_price=0.80)
        market = _make_market(prices=[0.60, 0.40])
        book_yes = _make_book("tok_yes", bid=0.59, ask=0.61, ask_size=600)
        book_no = _make_book("tok_no", bid=0.39, ask=0.41, ask_size=200)

        self.scanner._fetch_books = lambda tids: {"tok_yes": book_yes, "tok_no": book_no}
        signals = self.scanner.scan([market])

        sig = [s for s in signals if s.token_id == "tok_yes"][0]
        assert sig.tier == "highprob"
        assert sig.target_price == 0.78  # 0.60 * 1.30
        assert sig.stop_loss_price == 0.48  # 0.60 * 0.80
        assert sig.sized_usd == 8.0
