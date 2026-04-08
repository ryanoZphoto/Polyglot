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
        if len(value) >= 2 and (
            (value[0] == '"' and value[-1] == '"')
            or (value[0] == "'" and value[-1] == "'")
        ):
            value = value[1:-1]
        if key.startswith("EV_") or key.startswith("PM_"):
            os.environ[key] = value
            continue
        if key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class EVConfig:
    # Polymarket connection (shared PM_* vars)
    gamma_host: str
    clob_host: str
    chain_id: int
    private_key: str | None
    funder: str | None
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None

    # Bot mode
    mode: str  # "dry_run" or "live"
    dry_run: bool
    poll_interval_seconds: float
    log_json: bool

    # Market scanning
    scan_limit: int
    min_liquidity: float
    min_volume: float

    # Entry criteria -- what makes a contract worth buying
    max_entry_price: float      # only buy contracts cheaper than this (e.g. 0.40)
    min_entry_price: float      # skip dust-priced contracts (e.g. 0.01)
    min_book_depth_usd: float   # minimum ask-side depth in USD to enter
    min_spread_ratio: float     # spread/mid must be below this to avoid illiquid traps

    # Position sizing (tiered by price)
    position_size_usd: float    # default USD per entry
    longshot_size_usd: float    # USD for cheap longshot contracts
    highprob_size_usd: float    # USD for high-probability contracts
    longshot_ceiling: float     # price below this = longshot tier (e.g. 0.30)
    highprob_floor: float       # price above this = high-prob tier (e.g. 0.55)
    max_contracts_per_entry: float
    max_positions: int

    # Exit rules (tiered targets)
    take_profit_pct: float
    stop_loss_pct: float
    trailing_stop_pct: float
    longshot_take_profit_pct: float
    longshot_stop_loss_pct: float
    highprob_take_profit_pct: float
    highprob_stop_loss_pct: float

    # Risk limits
    max_total_exposure_usd: float
    max_daily_loss_usd: float
    max_per_market_usd: float
    emergency_stop: bool

    # Network
    request_timeout_seconds: float
    max_request_retries: int
    retry_backoff_seconds: float
    max_workers: int

    # Paths
    state_db_path: str
    log_path: str
    report_path: str

    @staticmethod
    def from_env() -> EVConfig:
        _load_dotenv()
        mode = os.getenv("EV_MODE", "dry_run").strip().lower()
        if mode not in {"dry_run", "live"}:
            raise ValueError("EV_MODE must be 'dry_run' or 'live'.")

        return EVConfig(
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
            poll_interval_seconds=float(os.getenv("EV_POLL_INTERVAL_SECONDS", "10.0")),
            log_json=_get_bool("EV_LOG_JSON", True),
            scan_limit=int(os.getenv("EV_SCAN_LIMIT", "300")),
            min_liquidity=float(os.getenv("EV_MIN_LIQUIDITY", "1000")),
            min_volume=float(os.getenv("EV_MIN_VOLUME", "500")),
            max_entry_price=float(os.getenv("EV_MAX_ENTRY_PRICE", "0.40")),
            min_entry_price=float(os.getenv("EV_MIN_ENTRY_PRICE", "0.02")),
            min_book_depth_usd=float(os.getenv("EV_MIN_BOOK_DEPTH_USD", "50")),
            min_spread_ratio=float(os.getenv("EV_MIN_SPREAD_RATIO", "0.30")),
            position_size_usd=float(os.getenv("EV_POSITION_SIZE_USD", "5.0")),
            longshot_size_usd=float(os.getenv("EV_LONGSHOT_SIZE_USD", "2.0")),
            highprob_size_usd=float(os.getenv("EV_HIGHPROB_SIZE_USD", "8.0")),
            longshot_ceiling=float(os.getenv("EV_LONGSHOT_CEILING", "0.30")),
            highprob_floor=float(os.getenv("EV_HIGHPROB_FLOOR", "0.55")),
            max_contracts_per_entry=float(os.getenv("EV_MAX_CONTRACTS_PER_ENTRY", "500")),
            max_positions=int(os.getenv("EV_MAX_POSITIONS", "20")),
            take_profit_pct=float(os.getenv("EV_TAKE_PROFIT_PCT", "0.50")),
            stop_loss_pct=float(os.getenv("EV_STOP_LOSS_PCT", "0.40")),
            trailing_stop_pct=float(os.getenv("EV_TRAILING_STOP_PCT", "0.0")),
            longshot_take_profit_pct=float(os.getenv("EV_LONGSHOT_TAKE_PROFIT_PCT", "1.50")),
            longshot_stop_loss_pct=float(os.getenv("EV_LONGSHOT_STOP_LOSS_PCT", "0.50")),
            highprob_take_profit_pct=float(os.getenv("EV_HIGHPROB_TAKE_PROFIT_PCT", "0.30")),
            highprob_stop_loss_pct=float(os.getenv("EV_HIGHPROB_STOP_LOSS_PCT", "0.20")),
            max_total_exposure_usd=float(os.getenv("EV_MAX_TOTAL_EXPOSURE_USD", "100.0")),
            max_daily_loss_usd=float(os.getenv("EV_MAX_DAILY_LOSS_USD", "25.0")),
            max_per_market_usd=float(os.getenv("EV_MAX_PER_MARKET_USD", "20.0")),
            emergency_stop=_get_bool("EV_EMERGENCY_STOP", False),
            request_timeout_seconds=float(os.getenv("EV_REQUEST_TIMEOUT_SECONDS", "8")),
            max_request_retries=int(os.getenv("EV_MAX_REQUEST_RETRIES", "2")),
            retry_backoff_seconds=float(os.getenv("EV_RETRY_BACKOFF_SECONDS", "0.3")),
            max_workers=int(os.getenv("EV_MAX_WORKERS", "10")),
            state_db_path=os.getenv("EV_STATE_DB_PATH", "ev_bot_state.sqlite3"),
            log_path=os.getenv("EV_LOG_PATH", "ev_runtime.log"),
            report_path=os.getenv("EV_REPORT_PATH", "ev_cycle_report.jsonl"),
        )
