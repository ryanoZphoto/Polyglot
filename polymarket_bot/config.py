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
    return [v.strip().lower() for v in value.split(",") if v.strip()]


def _load_local_dotenv() -> None:
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
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]

        # For bot configuration keys, always trust project-local .env so stale shell
        # environment values do not silently override dashboard/runtime expectations.
        if key.startswith("BOT_") or key.startswith("PM_"):
            os.environ[key] = value
            continue
        if key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class BotConfig:
    gamma_host: str
    clob_host: str
    chain_id: int
    mode: str
    poll_interval_seconds: float
    scan_limit: int
    min_liquidity: float
    min_edge: float
    min_profit_usd: float
    max_capital_per_trade: float
    max_bundle_shares: float
    min_group_size: int
    max_group_size: int
    max_opportunities_per_cycle: int
    market_cooldown_seconds: float
    request_timeout_seconds: float
    max_workers: int
    sports_only: bool
    include_keywords: List[str]
    enable_arb_scanner: bool
    enable_no_basket_strategy: bool
    enable_binary_pair_strategy: bool
    enable_multi_outcome_strategy: bool
    pair_min_edge: float
    pair_min_profit_usd: float
    multi_min_edge: float
    multi_min_profit_usd: float
    max_request_retries: int
    retry_backoff_seconds: float
    max_quote_fetch_latency_ms: float
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None
    log_json: bool
    state_db_path: str
    max_daily_loss_usd: float
    max_open_exposure_usd: float
    max_event_exposure_usd: float
    max_orders_per_minute: int
    emergency_stop: bool
    enable_llm_ranking: bool
    llm_provider: str
    llm_model: str
    llm_api_key: str | None
    llm_endpoint: str
    analysis_log_path: str
    bankroll_usd: float
    aggression: float
    min_net_profit_usd: float
    min_net_edge: float
    taker_fee_rate: float
    slippage_bps_base: float
    slippage_bps_per_leg: float
    slippage_bps_illiquidity: float
    latency_penalty_bps: float
    risk_buffer_bps: float
    enable_external_price_check: bool
    odds_api_key: str | None
    odds_regions: str
    odds_markets: str
    enable_leader_follow: bool
    leader_wallet: str
    leader_max_signal_age_seconds: int
    leader_min_notional_usd: float
    leader_price_tolerance_bps: float
    leader_alpha: float
    leader_max_signals_per_cycle: int
    leader_require_buy_side: bool
    enable_auto_tune: bool
    auto_tune_interval_cycles: int
    auto_tune_aggression_step: float
    auto_tune_profit_step: float
    auto_tune_edge_step: float
    auto_tune_min_aggression: float
    auto_tune_max_aggression: float
    auto_tune_min_net_profit_usd: float
    auto_tune_max_net_profit_usd: float
    auto_tune_min_net_edge: float
    auto_tune_max_net_edge: float
    dry_run: bool
    private_key: str | None
    funder: str | None

    @staticmethod
    def from_env() -> "BotConfig":
        _load_local_dotenv()
        mode = os.getenv("BOT_MODE", "dry_run").strip().lower()
        if mode not in {"dry_run", "live"}:
            raise ValueError("BOT_MODE must be either 'dry_run' or 'live'.")

        return BotConfig(
            gamma_host=os.getenv("PM_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/"),
            clob_host=os.getenv("PM_CLOB_HOST", "https://clob.polymarket.com").rstrip("/"),
            chain_id=int(os.getenv("PM_CHAIN_ID", "137")),
            mode=mode,
            poll_interval_seconds=float(os.getenv("BOT_POLL_INTERVAL_SECONDS", "2.0")),
            scan_limit=int(os.getenv("BOT_SCAN_LIMIT", "200")),
            min_liquidity=float(os.getenv("BOT_MIN_LIQUIDITY", "5000")),
            min_edge=float(os.getenv("BOT_MIN_EDGE", "0.01")),
            min_profit_usd=float(os.getenv("BOT_MIN_PROFIT_USD", "1.0")),
            max_capital_per_trade=float(os.getenv("BOT_MAX_CAPITAL_PER_TRADE", "100.0")),
            max_bundle_shares=float(os.getenv("BOT_MAX_BUNDLE_SHARES", "100.0")),
            min_group_size=int(os.getenv("BOT_MIN_GROUP_SIZE", "4")),
            max_group_size=int(os.getenv("BOT_MAX_GROUP_SIZE", "12")),
            max_opportunities_per_cycle=int(os.getenv("BOT_MAX_OPPS_PER_CYCLE", "3")),
            market_cooldown_seconds=float(os.getenv("BOT_MARKET_COOLDOWN_SECONDS", "20")),
            request_timeout_seconds=float(os.getenv("BOT_REQUEST_TIMEOUT_SECONDS", "10")),
            max_workers=int(os.getenv("BOT_MAX_WORKERS", "20")),
            sports_only=_get_bool("BOT_SPORTS_ONLY", True),
            include_keywords=_get_csv("BOT_INCLUDE_KEYWORDS"),
            enable_arb_scanner=_get_bool("BOT_ENABLE_ARB_SCANNER", True),
            enable_no_basket_strategy=_get_bool("BOT_ENABLE_NO_BASKET_STRATEGY", True),
            enable_binary_pair_strategy=_get_bool("BOT_ENABLE_BINARY_PAIR_STRATEGY", True),
            enable_multi_outcome_strategy=_get_bool("BOT_ENABLE_MULTI_OUTCOME_STRATEGY", True),
            pair_min_edge=float(os.getenv("BOT_PAIR_MIN_EDGE", "0.001")),
            pair_min_profit_usd=float(os.getenv("BOT_PAIR_MIN_PROFIT_USD", "0.01")),
            multi_min_edge=float(os.getenv("BOT_MULTI_MIN_EDGE", "0.001")),
            multi_min_profit_usd=float(os.getenv("BOT_MULTI_MIN_PROFIT_USD", "0.01")),
            max_request_retries=int(os.getenv("BOT_MAX_REQUEST_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("BOT_RETRY_BACKOFF_SECONDS", "0.35")),
            max_quote_fetch_latency_ms=float(os.getenv("BOT_MAX_QUOTE_FETCH_LATENCY_MS", "1500")),
            signature_type=int(os.getenv("PM_SIGNATURE_TYPE", "0")),
            api_key=os.getenv("PM_API_KEY"),
            api_secret=os.getenv("PM_API_SECRET"),
            api_passphrase=os.getenv("PM_API_PASSPHRASE"),
            log_json=_get_bool("BOT_LOG_JSON", True),
            state_db_path=os.getenv("BOT_STATE_DB_PATH", "polymarket_bot_state.sqlite3"),
            max_daily_loss_usd=float(os.getenv("BOT_MAX_DAILY_LOSS_USD", "250.0")),
            max_open_exposure_usd=float(os.getenv("BOT_MAX_OPEN_EXPOSURE_USD", "1000.0")),
            max_event_exposure_usd=float(os.getenv("BOT_MAX_EVENT_EXPOSURE_USD", "300.0")),
            max_orders_per_minute=int(os.getenv("BOT_MAX_ORDERS_PER_MINUTE", "15")),
            emergency_stop=_get_bool("BOT_EMERGENCY_STOP", False),
            enable_llm_ranking=_get_bool("BOT_ENABLE_LLM_RANKING", False),
            llm_provider=os.getenv("BOT_LLM_PROVIDER", "openai").strip().lower(),
            llm_model=os.getenv("BOT_LLM_MODEL", "gpt-4o-mini").strip(),
            llm_api_key=os.getenv("BOT_LLM_API_KEY"),
            llm_endpoint=os.getenv("BOT_LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions").strip(),
            analysis_log_path=os.getenv("BOT_ANALYSIS_LOG_PATH", "bot_cycle_report.jsonl").strip(),
            bankroll_usd=float(os.getenv("BOT_BANKROLL_USD", "100.0")),
            aggression=float(os.getenv("BOT_AGGRESSION", "0.5")),
            min_net_profit_usd=float(os.getenv("BOT_MIN_NET_PROFIT_USD", "0.25")),
            min_net_edge=float(os.getenv("BOT_MIN_NET_EDGE", "0.0025")),
            taker_fee_rate=float(os.getenv("BOT_TAKER_FEE_RATE", "0.03")),
            slippage_bps_base=float(os.getenv("BOT_SLIPPAGE_BPS_BASE", "5.0")),
            slippage_bps_per_leg=float(os.getenv("BOT_SLIPPAGE_BPS_PER_LEG", "2.0")),
            slippage_bps_illiquidity=float(os.getenv("BOT_SLIPPAGE_BPS_ILLIQUIDITY", "4000.0")),
            latency_penalty_bps=float(os.getenv("BOT_LATENCY_PENALTY_BPS", "4.0")),
            risk_buffer_bps=float(os.getenv("BOT_RISK_BUFFER_BPS", "8.0")),
            enable_external_price_check=_get_bool("BOT_ENABLE_EXTERNAL_PRICE_CHECK", False),
            odds_api_key=os.getenv("BOT_ODDS_API_KEY"),
            odds_regions=os.getenv("BOT_ODDS_REGIONS", "us,uk,eu"),
            odds_markets=os.getenv("BOT_ODDS_MARKETS", "h2h"),
            enable_leader_follow=_get_bool("BOT_ENABLE_LEADER_FOLLOW", False),
            leader_wallet=os.getenv("BOT_LEADER_WALLET", "").strip().lower(),
            leader_max_signal_age_seconds=int(os.getenv("BOT_LEADER_MAX_SIGNAL_AGE_SECONDS", "180")),
            leader_min_notional_usd=float(os.getenv("BOT_LEADER_MIN_NOTIONAL_USD", "100.0")),
            leader_price_tolerance_bps=float(os.getenv("BOT_LEADER_PRICE_TOLERANCE_BPS", "80.0")),
            leader_alpha=float(os.getenv("BOT_LEADER_ALPHA", "0.03")),
            leader_max_signals_per_cycle=int(os.getenv("BOT_LEADER_MAX_SIGNALS_PER_CYCLE", "5")),
            leader_require_buy_side=_get_bool("BOT_LEADER_REQUIRE_BUY_SIDE", True),
            enable_auto_tune=_get_bool("BOT_ENABLE_AUTO_TUNE", True),
            auto_tune_interval_cycles=int(os.getenv("BOT_AUTO_TUNE_INTERVAL_CYCLES", "3")),
            auto_tune_aggression_step=float(os.getenv("BOT_AUTO_TUNE_AGGRESSION_STEP", "0.03")),
            auto_tune_profit_step=float(os.getenv("BOT_AUTO_TUNE_PROFIT_STEP", "0.03")),
            auto_tune_edge_step=float(os.getenv("BOT_AUTO_TUNE_EDGE_STEP", "0.0004")),
            auto_tune_min_aggression=float(os.getenv("BOT_AUTO_TUNE_MIN_AGGRESSION", "0.55")),
            auto_tune_max_aggression=float(os.getenv("BOT_AUTO_TUNE_MAX_AGGRESSION", "0.97")),
            auto_tune_min_net_profit_usd=float(os.getenv("BOT_AUTO_TUNE_MIN_NET_PROFIT_USD", "0.08")),
            auto_tune_max_net_profit_usd=float(os.getenv("BOT_AUTO_TUNE_MAX_NET_PROFIT_USD", "0.75")),
            auto_tune_min_net_edge=float(os.getenv("BOT_AUTO_TUNE_MIN_NET_EDGE", "0.0012")),
            auto_tune_max_net_edge=float(os.getenv("BOT_AUTO_TUNE_MAX_NET_EDGE", "0.0100")),
            dry_run=mode == "dry_run",
            private_key=os.getenv("PM_PRIVATE_KEY"),
            funder=os.getenv("PM_FUNDER"),
        )
