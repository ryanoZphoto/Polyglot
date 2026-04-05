# Polymarket Arbitrage Bot (Replication Starter)

This repository contains a production-style starter for replicating a high-frequency
Polymarket arbitrage pattern based on cross-market structure in binary sports books.

## Strategy implemented (no fallback)

- Fetch active markets from Gamma API.
- Filter for tradable markets (`active`, `acceptingOrders`, binary Yes/No, minimum liquidity).
- Optionally restrict to sports-style markets (default: enabled).
- Pull best asks for each outcome token from CLOB `/book`.
- Group markets into event clusters and build NO-baskets (buy NO in multiple mutually-exclusive markets).
- Detect opportunities where:
  - NO-basket `sum(best_asks) < 1 - BOT_MIN_EDGE`
  - expected guaranteed profit exceeds `BOT_MIN_PROFIT_USD`
- Size basket shares with:
  - `BOT_MAX_CAPITAL_PER_TRADE`
  - `BOT_MAX_BUNDLE_SHARES`

## Quickstart

1. Install dependencies:
   - `python3 -m pip install -r requirements.txt`
2. Dry-run one cycle:
   - `python3 -m polymarket_bot.main --once`
3. Continuous dry-run:
   - `python3 -m polymarket_bot.main`

## Environment variables

- `BOT_MODE`: `dry_run` (default) or `live`
- `BOT_SCAN_LIMIT`: number of markets to scan each cycle (default `200`)
- `BOT_MIN_LIQUIDITY`: minimum market liquidity (default `5000`)
- `BOT_MIN_EDGE`: minimum bundle edge (default `0.01`)
- `BOT_MIN_PROFIT_USD`: minimum guaranteed profit in USD (default `1.0`)
- `BOT_MAX_CAPITAL_PER_TRADE`: max USD deployed per opportunity (default `100`)
- `BOT_MAX_BUNDLE_SHARES`: max bundle shares per opportunity (default `100`)
- `BOT_MAX_OPPS_PER_CYCLE`: cap fills per cycle (default `3`)
- `BOT_MARKET_COOLDOWN_SECONDS`: avoid immediate re-trading same market (default `20`)
- `BOT_POLL_INTERVAL_SECONDS`: polling interval for loop mode (default `2`)
- `BOT_SPORTS_ONLY`: `true`/`false` (default `true`)
- `BOT_INCLUDE_KEYWORDS`: optional comma-separated text filter
- `BOT_REQUEST_TIMEOUT_SECONDS`: HTTP timeout for API calls (default `10`)
- `BOT_MAX_WORKERS`: thread workers for per-market analysis (default `20`)
- `BOT_MIN_GROUP_SIZE`: min markets in an event group for basket eval (default `4`)
- `BOT_MAX_GROUP_SIZE`: max markets in an event group for basket eval (default `12`)

## Live mode

`BOT_MODE=live` requires valid Polymarket trading credentials:

- `PM_PRIVATE_KEY`
- optionally `PM_FUNDER`
- `PM_CHAIN_ID` (default `137`)
- `PM_SIGNATURE_TYPE`:
  - `0` = EOA
  - `1` = POLY_PROXY
  - `2` = POLY_GNOSIS_SAFE (default)
- Optional pre-provisioned API credentials:
  - `PM_API_KEY`
  - `PM_API_SECRET`
  - `PM_API_PASSPHRASE`

Order placement uses `py-clob-client` and submits one BUY order per NO leg in the selected basket.
By default orders are posted as `GTC` limit orders using each discovered best ask price.

Key type requirements:
- Use an EVM private key (Ethereum/Polygon format, 64 hex chars, optionally `0x` prefix).
- Solana keys (including Phantom Solana private keys/seed exports) are not compatible.

Recommended setup process:
1. Start in `BOT_MODE=dry_run`.
2. Set very small `BOT_MAX_CAPITAL_PER_TRADE`.
3. Test `--once` in live mode with small limits and inspect resulting order IDs.
4. Only then run the continuous loop.