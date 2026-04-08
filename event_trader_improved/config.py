"""Enhanced config with edge calculation and volatility parameters."""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Literal

@dataclass
class EVConfig:
    # API endpoints (required, no defaults)
    gamma_host: str
    clob_host: str
    chain_id: int
    private_key: str | None
    funder: str
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None

    # Runtime (required)
    mode: Literal["dry_run", "live"]
    dry_run: bool
    poll_interval_seconds: float
    log_json: bool

    # Market scanning (required)
    scan_limit: int
    min_liquidity: float
    min_volume: float

    # Entry criteria (required)
    max_entry_price: float
    min_entry_price: float
    min_book_depth_usd: float
    min_spread_ratio: float
    
    # Position sizing (required)
    position_size_usd: float
    longshot_size_usd: float
    highprob_size_usd: float
    longshot_ceiling: float
    highprob_floor: float
    max_contracts_per_entry: float
    max_positions: int

    # Exit rules (required)
    take_profit_pct: float
    stop_loss_pct: float
    trailing_stop_pct: float
    longshot_take_profit_pct: float
    longshot_stop_loss_pct: float
    highprob_take_profit_pct: float
    highprob_stop_loss_pct: float

    # Risk limits (required)
    max_total_exposure_usd: float
    max_daily_loss_usd: float
    max_per_market_usd: float
    emergency_stop: bool

    # Network (required)
    request_timeout_seconds: float
    max_request_retries: int
    retry_backoff_seconds: float
    max_workers: int

    # Persistence (required)
    state_db_path: str
    log_path: str
    report_path: str
    
    # NEW FIELDS - All with defaults (must come LAST)
    min_edge: float = 0.05
    max_spread_pct: float = 0.05
    min_volume_24h: float = 100.0
    use_kelly_sizing: bool = True
    kelly_fraction: float = 0.25
    target_volatility: float = 0.15
    max_position_pct: float = 0.10
    trailing_activation_pct: float = 0.05
    time_decay_days: int = 1
    time_decay_stop_pct: float = 0.05
    volatility_multiplier: float = 2.0

    @classmethod
    def from_env(cls) -> EVConfig:
        """Load from environment variables."""
        return cls(
            gamma_host=os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com"),
            clob_host=os.getenv("CLOB_HOST", "https://clob.polymarket.com"),
            chain_id=int(os.getenv("CHAIN_ID", "137")),
            private_key=os.getenv("PRIVATE_KEY"),
            funder=os.getenv("FUNDER", ""),
            signature_type=int(os.getenv("SIGNATURE_TYPE", "0")),
            api_key=os.getenv("API_KEY"),
            api_secret=os.getenv("API_SECRET"),
            api_passphrase=os.getenv("API_PASSPHRASE"),
            mode=os.getenv("MODE", "dry_run"),
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
            poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "10")),
            log_json=os.getenv("LOG_JSON", "false").lower() == "true",
            scan_limit=int(os.getenv("SCAN_LIMIT", "100")),
            min_liquidity=float(os.getenv("MIN_LIQUIDITY", "1000")),
            min_volume=float(os.getenv("MIN_VOLUME", "500")),
            max_entry_price=float(os.getenv("MAX_ENTRY_PRICE", "0.40")),
            min_entry_price=float(os.getenv("MIN_ENTRY_PRICE", "0.02")),
            min_book_depth_usd=float(os.getenv("MIN_BOOK_DEPTH_USD", "50")),
            min_spread_ratio=float(os.getenv("MIN_SPREAD_RATIO", "0.30")),
            position_size_usd=float(os.getenv("POSITION_SIZE_USD", "5")),
            longshot_size_usd=float(os.getenv("LONGSHOT_SIZE_USD", "2")),
            highprob_size_usd=float(os.getenv("HIGHPROB_SIZE_USD", "8")),
            longshot_ceiling=float(os.getenv("LONGSHOT_CEILING", "0.30")),
            highprob_floor=float(os.getenv("HIGHPROB_FLOOR", "0.55")),
            max_contracts_per_entry=float(os.getenv("MAX_CONTRACTS_PER_ENTRY", "100")),
            max_positions=int(os.getenv("MAX_POSITIONS", "10")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.50")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.40")),
            trailing_stop_pct=float(os.getenv("TRAILING_STOP_PCT", "0.20")),
            longshot_take_profit_pct=float(os.getenv("LONGSHOT_TAKE_PROFIT_PCT", "1.50")),
            longshot_stop_loss_pct=float(os.getenv("LONGSHOT_STOP_LOSS_PCT", "0.50")),
            highprob_take_profit_pct=float(os.getenv("HIGHPROB_TAKE_PROFIT_PCT", "0.30")),
            highprob_stop_loss_pct=float(os.getenv("HIGHPROB_STOP_LOSS_PCT", "0.20")),
            max_total_exposure_usd=float(os.getenv("MAX_TOTAL_EXPOSURE_USD", "100")),
            max_daily_loss_usd=float(os.getenv("MAX_DAILY_LOSS_USD", "25")),
            max_per_market_usd=float(os.getenv("MAX_PER_MARKET_USD", "20")),
            emergency_stop=os.getenv("EMERGENCY_STOP", "false").lower() == "true",
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "8")),
            max_request_retries=int(os.getenv("MAX_REQUEST_RETRIES", "2")),
            retry_backoff_seconds=float(os.getenv("RETRY_BACKOFF_SECONDS", "0.3")),
            max_workers=int(os.getenv("MAX_WORKERS", "4")),
            state_db_path=os.getenv("STATE_DB_PATH", "ev_state.db"),
            log_path=os.getenv("LOG_PATH", "ev_trader.log"),
            report_path=os.getenv("REPORT_PATH", "ev_report.jsonl"),
        )

