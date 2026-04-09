"""Configuration for improved event trader."""

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    # Use the .env file in the same directory as this file
    dotenv_path = Path(__file__).parent / ".env"
    if not dotenv_path.exists():
        # Fallback to current working directory if not found in package
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
        if key.startswith("EV_") or key.startswith("PM_"):
            os.environ[key] = value
            continue
        if key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class ImprovedEVConfig:
    """Complete config for improved event trader with ALL required fields."""
    
    # API endpoints
    gamma_host: str
    clob_host: str
    chain_id: int
    
    # Credentials
    private_key: str | None
    funder: str | None
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None
    
    # Mode
    mode: str
    dry_run: bool
    
    # Timing
    poll_interval_seconds: float
    request_timeout_seconds: float
    max_request_retries: int
    retry_backoff_seconds: float
    
    # Performance
    max_workers: int
    
    # Paths
    state_db_path: str
    log_path: str
    report_path: str
    log_json: bool
    
    # Market scanning
    scan_limit: int
    
    # Entry filters
    max_entry_price: float
    min_edge: float
    max_spread_pct: float
    min_volume_24h: float
    
    # Position sizing
    max_position_size_usd: float
    max_total_exposure_usd: float
    max_positions: int
    use_kelly_sizing: bool
    kelly_fraction: float

    # Strategies
    enable_binary_pair: bool
    enable_multi_outcome: bool
    enable_single_outcome: bool
    
    # Advanced Optimizations ("The Genius Settings")
    momentum_lookback_cycles: int
    momentum_min_velocity: float
    aggressive_trailing_activation: float
    break_even_buffer: float
    min_profit_lock: float
    
    @staticmethod
    def from_env() -> "ImprovedEVConfig":
        _load_dotenv()  # ADD THIS LINE
        mode = os.getenv("EV_MODE", "dry_run")
        
        return ImprovedEVConfig(
            # API
            gamma_host=os.getenv("PM_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/"),
            clob_host=os.getenv("PM_CLOB_HOST", "https://clob.polymarket.com").rstrip("/"),
            chain_id=int(os.getenv("PM_CHAIN_ID", "137")),
            
            # Credentials
            private_key=os.getenv("PM_PRIVATE_KEY"),
            funder=os.getenv("PM_FUNDER"),
            signature_type=int(os.getenv("PM_SIGNATURE_TYPE", "0")),
            api_key=os.getenv("PM_API_KEY"),
            api_secret=os.getenv("PM_API_SECRET"),
            api_passphrase=os.getenv("PM_API_PASSPHRASE"),
            
            # Mode
            mode=mode,
            dry_run=(mode == "dry_run"),
            
            # Timing
            poll_interval_seconds=float(os.getenv("EV_POLL_INTERVAL_SECONDS", "20.0")),
            request_timeout_seconds=float(os.getenv("EV_REQUEST_TIMEOUT_SECONDS", "10.0")),
            max_request_retries=int(os.getenv("EV_MAX_REQUEST_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("EV_RETRY_BACKOFF_SECONDS", "2.0")),
            
            # Performance
            max_workers=int(os.getenv("EV_MAX_WORKERS", "10")),
            
            # Paths
            state_db_path=os.getenv("EV_STATE_DB_PATH", "ev_improved_state.sqlite3"),
            log_path=os.getenv("EV_LOG_PATH", "ev_improved.log"),
            report_path=os.getenv("EV_REPORT_PATH", "ev_improved_report.jsonl"),
            log_json=os.getenv("EV_LOG_JSON", "true").lower() == "true",
            
            # Market scanning
            scan_limit=int(os.getenv("EV_SCAN_LIMIT", "300")),
            
            # Entry filters
            max_entry_price=float(os.getenv("EV_MAX_ENTRY_PRICE", "0.30")),
            min_edge=float(os.getenv("EV_MIN_EDGE", "0.05")),
            max_spread_pct=float(os.getenv("EV_MAX_SPREAD_PCT", "0.10")),
            min_volume_24h=float(os.getenv("EV_MIN_VOLUME_24H", "1000.0")),
            
            # Position sizing
            max_position_size_usd=float(os.getenv("EV_MAX_POSITION_SIZE_USD", "100.0")),
            max_total_exposure_usd=float(os.getenv("EV_MAX_TOTAL_EXPOSURE_USD", "1000.0")),
            max_positions=int(os.getenv("EV_MAX_POSITIONS", "10")),
            use_kelly_sizing=os.getenv("EV_USE_KELLY_SIZING", "true").lower() == "true",
            kelly_fraction=float(os.getenv("EV_KELLY_FRACTION", "0.25")),
            
            # Strategies
            enable_binary_pair=os.getenv("EV_ENABLE_BINARY_PAIR", "true").lower() == "true",
            enable_multi_outcome=os.getenv("EV_ENABLE_MULTI_OUTCOME", "true").lower() == "true",
            enable_single_outcome=os.getenv("EV_ENABLE_SINGLE", "true").lower() == "true",

            # Advanced Optimizations
            momentum_lookback_cycles=int(os.getenv("EV_MOMENTUM_LOOKBACK", "3")),
            momentum_min_velocity=float(os.getenv("EV_MOMENTUM_MIN_VELOCITY", "0.01")),
            aggressive_trailing_activation=float(os.getenv("EV_TRAILING_ACTIVATION", "0.02")),
            break_even_buffer=float(os.getenv("EV_BREAK_EVEN_BUFFER", "0.005")),
            min_profit_lock=float(os.getenv("EV_MIN_PROFIT_LOCK", "0.01")),
        )






