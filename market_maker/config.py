from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_csv(name: str, default: str = "") -> List[str]:
    value = os.getenv(name, default).strip()
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _load_dotenv() -> None:
    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.exists():
        return
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]
        if key.startswith("MM_") or key.startswith("PM_"):
            os.environ[key] = value
            continue
        if key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class MMConfig:
    # Polymarket connection (shared with arb bot via PM_* vars)
    gamma_host: str
    clob_host: str
    chain_id: int
    private_key: str | None
    funder: str | None
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None

    # MM bot mode
    mode: str  # "dry_run" or "live"
    dry_run: bool
    poll_interval_seconds: float
    log_json: bool

    # Market selection
    markets: List[str]  # slugs, or ["auto"]
    min_liquidity: float
    scan_limit: int

    # Quoting parameters
    half_spread: float
    quote_size: float
    min_book_spread: float  # don't quote if book spread is narrower than this

    # Inventory management
    max_inventory_per_market: float
    skew_factor: float

    # Risk limits
    max_total_exposure_usd: float
    max_open_orders: int
    max_daily_loss_usd: float
    max_orders_per_minute: int
    emergency_stop: bool

    # Network
    request_timeout_seconds: float
    max_request_retries: int
    retry_backoff_seconds: float
    max_workers: int

    # Paths (all separate from arb bot)
    state_db_path: str
    log_path: str
    report_path: str

    @staticmethod
    def from_env() -> MMConfig:
        _load_dotenv()
        mode = os.getenv("MM_MODE", "dry_run").strip().lower()
        if mode not in {"dry_run", "live"}:
            raise ValueError("MM_MODE must be 'dry_run' or 'live'.")

        return MMConfig(
            gamma_host=os.getenv("PM_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/"),
            clob_host=os.getenv("PM_CLOB_HOST", "https://clob.polymarket.com").rstrip("/"),
            chain_id=int(os.getenv("PM_CHAIN_ID", "137")),
            private_key=os.getenv("PM_PRIVATE_KEY"),
            funder=os.getenv("PM_FUNDER"),
            signature_type=int(os.getenv("PM_SIGNATURE_TYPE", "0")),
            api_key=os.getenv("PM_API_KEY"),
            api_secret=os.getenv("PM_API_SECRET"),
            api_passphrase=os.getenv("PM_API_PASSPHRASE"),
            mode=mode,
            dry_run=mode == "dry_run",
            poll_interval_seconds=float(os.getenv("MM_POLL_INTERVAL_SECONDS", "5.0")),
            log_json=_get_bool("MM_LOG_JSON", True),
            markets=_get_csv("MM_MARKETS", "auto"),
            min_liquidity=float(os.getenv("MM_MIN_LIQUIDITY", "5000")),
            scan_limit=int(os.getenv("MM_SCAN_LIMIT", "200")),
            half_spread=float(os.getenv("MM_HALF_SPREAD", "0.02")),
            quote_size=float(os.getenv("MM_QUOTE_SIZE", "20.0")),
            min_book_spread=float(os.getenv("MM_MIN_BOOK_SPREAD", "0.01")),
            max_inventory_per_market=float(os.getenv("MM_MAX_INVENTORY_PER_MARKET", "100.0")),
            skew_factor=float(os.getenv("MM_SKEW_FACTOR", "0.5")),
            max_total_exposure_usd=float(os.getenv("MM_MAX_TOTAL_EXPOSURE_USD", "500.0")),
            max_open_orders=int(os.getenv("MM_MAX_OPEN_ORDERS", "800")),
            max_daily_loss_usd=float(os.getenv("MM_MAX_DAILY_LOSS_USD", "50.0")),
            max_orders_per_minute=int(os.getenv("MM_MAX_ORDERS_PER_MINUTE", "30")),
            emergency_stop=_get_bool("MM_EMERGENCY_STOP", False),
            request_timeout_seconds=float(os.getenv("MM_REQUEST_TIMEOUT_SECONDS", "8")),
            max_request_retries=int(os.getenv("MM_MAX_REQUEST_RETRIES", "2")),
            retry_backoff_seconds=float(os.getenv("MM_RETRY_BACKOFF_SECONDS", "0.3")),
            max_workers=int(os.getenv("MM_MAX_WORKERS", "10")),
            state_db_path=os.getenv("MM_STATE_DB_PATH", "mm_bot_state.sqlite3"),
            log_path=os.getenv("MM_LOG_PATH", "mm_runtime.log"),
            report_path=os.getenv("MM_REPORT_PATH", "mm_cycle_report.jsonl"),
        )
