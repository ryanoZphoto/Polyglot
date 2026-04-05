from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_WEB = "https://polymarket.com"


@dataclass
class TrackerConfig:
    user: str
    limit: int
    offset: int
    outdir: Path
    timeout_seconds: float


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return cleaned or "unknown"


def _safe_get(record: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _profile_matches_username(profile: dict[str, Any], username: str) -> bool:
    username_lc = username.lower().strip("@")
    if not username_lc:
        return False
    direct_fields = [
        str(profile.get("name", "")),
        str(profile.get("username", "")),
        str(profile.get("xUsername", "")),
        str(profile.get("pseudonym", "")),
    ]
    for value in direct_fields:
        if value.lower().strip("@") == username_lc:
            return True
    users = profile.get("users")
    if isinstance(users, list):
        for user_obj in users:
            if not isinstance(user_obj, dict):
                continue
            for key in ("name", "username", "xUsername", "pseudonym"):
                val = str(user_obj.get(key, ""))
                if val.lower().strip("@") == username_lc:
                    return True
    return False


def _resolve_wallet(user: str, timeout_seconds: float) -> str:
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", user):
        return user.lower()

    username = user.strip()
    if username.startswith("@"):
        username = username[1:]
    url = f"{POLYMARKET_WEB}/@{username}"
    resp = requests.get(url, timeout=timeout_seconds)
    resp.raise_for_status()

    # Profile pages can include multiple wallet addresses in bundled scripts.
    matches = re.findall(r"0x[a-fA-F0-9]{40}", resp.text)
    if not matches:
        raise RuntimeError(f"Could not resolve wallet address for username '{user}'.")
    candidates = list(dict.fromkeys(addr.lower() for addr in matches))
    if len(candidates) == 1:
        return candidates[0]

    for candidate in candidates:
        try:
            profile = _fetch_profile(candidate, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        if _profile_matches_username(profile, username):
            return candidate

    # Fallback: keep deterministic ordering.
    return candidates[0]


def _fetch_json(url: str, params: dict[str, Any], timeout_seconds: float) -> Any:
    resp = requests.get(url, params=params, timeout=timeout_seconds)
    resp.raise_for_status()
    return resp.json()


def _fetch_profile(address: str, timeout_seconds: float) -> dict[str, Any]:
    payload = _fetch_json(
        f"{GAMMA_API}/public-profile",
        {"address": address},
        timeout_seconds=timeout_seconds,
    )
    return payload if isinstance(payload, dict) else {}


def _fetch_activity(address: str, limit: int, offset: int, timeout_seconds: float) -> list[dict[str, Any]]:
    payload = _fetch_json(
        f"{DATA_API}/activity",
        {"user": address, "limit": limit, "offset": offset, "sortDirection": "DESC"},
        timeout_seconds=timeout_seconds,
    )
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _fetch_positions(address: str, limit: int, timeout_seconds: float) -> list[dict[str, Any]]:
    payload = _fetch_json(
        f"{DATA_API}/positions",
        {"user": address, "limit": limit, "offset": 0, "sortBy": "CURRENT", "sortDirection": "DESC"},
        timeout_seconds=timeout_seconds,
    )
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _normalize_activity_rows(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in activity:
        timestamp_raw = _safe_get(rec, ["timestamp", "createdAt", "time"])
        timestamp = str(timestamp_raw) if timestamp_raw is not None else ""
        title = str(_safe_get(rec, ["title", "marketTitle", "market", "question"], ""))
        outcome = str(_safe_get(rec, ["outcome", "outcomeName", "tokenName"], ""))
        side = str(_safe_get(rec, ["side", "action", "type"], ""))
        price = _to_float(_safe_get(rec, ["price", "avgPrice", "executionPrice"], 0.0), 0.0)
        size = _to_float(_safe_get(rec, ["size", "tokens", "amount", "shares"], 0.0), 0.0)
        notional = _to_float(_safe_get(rec, ["usdcSize", "cash", "notional", "amount"], 0.0), 0.0)
        market_slug = str(_safe_get(rec, ["slug", "marketSlug"], ""))
        event_slug = str(_safe_get(rec, ["eventSlug"], ""))
        rows.append(
            {
                "timestamp": timestamp,
                "title": title,
                "market_slug": market_slug,
                "event_slug": event_slug,
                "outcome": outcome,
                "side": side.upper(),
                "price": price,
                "size": size,
                "notional_usd": notional,
            }
        )
    return rows


def _normalize_position_rows(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in positions:
        rows.append(
            {
                "title": str(_safe_get(rec, ["title", "marketTitle", "question"], "")),
                "slug": str(_safe_get(rec, ["slug", "marketSlug"], "")),
                "outcome": str(_safe_get(rec, ["outcome", "outcomeName", "tokenName"], "")),
                "avg_price": _to_float(_safe_get(rec, ["avgPrice", "averagePrice", "price"], 0.0), 0.0),
                "current_price": _to_float(_safe_get(rec, ["current", "currentPrice"], 0.0), 0.0),
                "size": _to_float(_safe_get(rec, ["size", "tokens", "shares"], 0.0), 0.0),
                "value_usd": _to_float(_safe_get(rec, ["value", "currentValue", "amount"], 0.0), 0.0),
                "cash_pnl": _to_float(_safe_get(rec, ["cashPnl", "pnl", "cashPNL"], 0.0), 0.0),
                "percent_pnl": _to_float(_safe_get(rec, ["percentPnl", "percentPNL"], 0.0), 0.0),
            }
        )
    return rows


def _infer_strategy_signals(activity_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    side_counter: Counter[str] = Counter()
    price_bands: Counter[str] = Counter()
    top_titles: Counter[str] = Counter()
    hourly_activity: Counter[str] = Counter()

    for row in activity_rows:
        market_key = row["market_slug"] or row["title"]
        if market_key:
            by_market[market_key].append(row)
        side_counter[row["side"]] += 1
        top_titles[row["title"]] += 1

        p = row["price"]
        if p < 0.2:
            band = "<0.20"
        elif p < 0.4:
            band = "0.20-0.39"
        elif p < 0.6:
            band = "0.40-0.59"
        elif p < 0.8:
            band = "0.60-0.79"
        else:
            band = ">=0.80"
        price_bands[band] += 1

        ts = row["timestamp"]
        hour_key = ts[:13] if len(ts) >= 13 else "unknown"
        hourly_activity[hour_key] += 1

    ladder_markets = []
    repeated_buys = 0
    for market_key, rows in by_market.items():
        buys = [r for r in rows if r["side"] == "BUY"]
        if len(buys) >= 3:
            repeated_buys += len(buys)
            ladder_markets.append(
                {
                    "market": market_key,
                    "buy_count": len(buys),
                    "avg_buy_price": sum(r["price"] for r in buys) / max(1, len(buys)),
                    "total_notional_usd": sum(r["notional_usd"] for r in buys),
                }
            )
    ladder_markets.sort(key=lambda x: x["buy_count"], reverse=True)

    likely_modes: list[str] = []
    if side_counter.get("BUY", 0) > max(1, side_counter.get("SELL", 0)) * 2:
        likely_modes.append("Net-long momentum/conviction bias (buy-heavy flow)")
    if ladder_markets:
        likely_modes.append("Laddered entries / scaling into positions")
    if price_bands.get(">=0.80", 0) >= price_bands.get("<0.20", 0):
        likely_modes.append("High-probability favorites preference")
    if not likely_modes:
        likely_modes.append("Mixed flow; no strong single-mode inference")

    return {
        "activity_count": len(activity_rows),
        "side_distribution": dict(side_counter),
        "price_band_distribution": dict(price_bands),
        "top_traded_titles": top_titles.most_common(15),
        "top_active_hours": hourly_activity.most_common(20),
        "ladder_markets": ladder_markets[:15],
        "likely_strategy_modes": likely_modes,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown_report(path: Path, snapshot: dict[str, Any]) -> None:
    analysis = snapshot["analysis"]
    profile = snapshot["profile"]
    lines = [
        "# Trader Activity Report",
        "",
        f"- Generated: `{snapshot['generated_at']}`",
        f"- User input: `{snapshot['user_input']}`",
        f"- Resolved wallet: `{snapshot['resolved_wallet']}`",
        f"- Display name: `{profile.get('name', '')}`",
        f"- Activity records fetched: `{analysis['activity_count']}`",
        "",
        "## Likely Strategy Modes",
    ]
    for mode in analysis["likely_strategy_modes"]:
        lines.append(f"- {mode}")

    lines.append("")
    lines.append("## Side Distribution")
    for side, count in analysis["side_distribution"].items():
        lines.append(f"- {side}: {count}")

    lines.append("")
    lines.append("## Price Bands")
    for band, count in analysis["price_band_distribution"].items():
        lines.append(f"- {band}: {count}")

    lines.append("")
    lines.append("## Top Ladder Markets")
    for row in analysis["ladder_markets"][:10]:
        lines.append(
            f"- {row['market']} | buys={row['buy_count']} | avg={row['avg_buy_price']:.4f} | "
            f"notional=${row['total_notional_usd']:.2f}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(config: TrackerConfig) -> dict[str, Any]:
    config.outdir.mkdir(parents=True, exist_ok=True)
    wallet = _resolve_wallet(config.user, timeout_seconds=config.timeout_seconds)
    profile = _fetch_profile(wallet, timeout_seconds=config.timeout_seconds)
    activity_raw = _fetch_activity(
        wallet,
        limit=config.limit,
        offset=config.offset,
        timeout_seconds=config.timeout_seconds,
    )
    positions_raw = _fetch_positions(wallet, limit=config.limit, timeout_seconds=config.timeout_seconds)

    activity_rows = _normalize_activity_rows(activity_raw)
    position_rows = _normalize_position_rows(positions_raw)
    analysis = _infer_strategy_signals(activity_rows)

    snapshot = {
        "generated_at": _now_utc(),
        "user_input": config.user,
        "resolved_wallet": wallet,
        "profile": profile,
        "analysis": analysis,
        "meta": {
            "activity_limit": config.limit,
            "activity_offset": config.offset,
            "position_limit": config.limit,
        },
    }

    base = _slug(config.user)
    _write_csv(config.outdir / f"{base}_activity.csv", activity_rows)
    _write_csv(config.outdir / f"{base}_positions.csv", position_rows)
    (config.outdir / f"{base}_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown_report(config.outdir / f"{base}_report.md", snapshot)
    return snapshot


def parse_args() -> TrackerConfig:
    parser = argparse.ArgumentParser(
        description="Track and analyze a Polymarket trader's public activity.",
    )
    parser.add_argument("--user", required=True, help="Username like @sovereign2013 or wallet address.")
    parser.add_argument("--limit", type=int, default=500, help="Max activity/positions records to fetch.")
    parser.add_argument("--offset", type=int, default=0, help="Activity pagination offset.")
    parser.add_argument(
        "--outdir",
        default="artifacts/trader_activity",
        help="Output directory for CSV, JSON snapshot, and report.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="HTTP timeout.")
    args = parser.parse_args()
    return TrackerConfig(
        user=args.user,
        limit=max(1, min(args.limit, 500)),
        offset=max(0, args.offset),
        outdir=Path(args.outdir),
        timeout_seconds=max(1.0, args.timeout_seconds),
    )


if __name__ == "__main__":
    cfg = parse_args()
    result = run(cfg)
    print(
        json.dumps(
            {
                "ok": True,
                "user_input": result["user_input"],
                "resolved_wallet": result["resolved_wallet"],
                "activity_count": result["analysis"]["activity_count"],
                "outdir": str(cfg.outdir),
            },
            ensure_ascii=True,
        )
    )
