from __future__ import annotations

import os
from dataclasses import dataclass
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
    signature_type: int
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None
    dry_run: bool
    private_key: str | None
    funder: str | None

    @staticmethod
    def from_env() -> "BotConfig":
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
            signature_type=int(os.getenv("PM_SIGNATURE_TYPE", "0")),
            api_key=os.getenv("PM_API_KEY"),
            api_secret=os.getenv("PM_API_SECRET"),
            api_passphrase=os.getenv("PM_API_PASSPHRASE"),
            dry_run=mode == "dry_run",
            private_key=os.getenv("PM_PRIVATE_KEY"),
            funder=os.getenv("PM_FUNDER"),
        )
