# Polymarket Arbitrage Bot (Hardened Hybrid)

This project recreates a high-frequency Polymarket NO-basket arbitrage bot with a hardened runtime:
- deterministic opportunity scanner
- mandatory risk gates
- robust execution lifecycle
- persisted state in SQLite
- structured logging
- optional LLM ranking layer (never bypasses risk rules)

## Core strategy

- Pull active markets from Gamma.
- Parse only tradable binary Yes/No markets with orderbook enabled.
- Fetch NO-leg best asks from CLOB.
- Group by event and evaluate NO-basket opportunities.
- Trade only when deterministic edge/profit constraints pass.
- Apply portfolio-level risk checks before every trade.

## Runtime architecture

- `polymarket_bot/runtime.py`: cycle orchestration.
- `polymarket_bot/scanner.py`: deterministic opportunity generation.
- `polymarket_bot/risk.py`: hard limits and emergency stop.
- `polymarket_bot/execution.py`: dry-run/live execution with partial-failure mitigation.
- `polymarket_bot/reconcile.py`: trade result reconciliation into state.
- `polymarket_bot/state.py`: SQLite audit trail (cycles, trades, orders, fills).
- `polymarket_bot/llm_ranker.py`: optional ranking/filtering layer.

## Quickstart

1. Install dependencies:
   - `python3 -m pip install -r requirements.txt`
2. Run one dry cycle:
   - `python3 -m polymarket_bot.main --once`
3. Run dry loop:
   - `python3 -m polymarket_bot.main`
4. Inspect persisted status:
   - `python3 -m polymarket_bot.main --status`

## Dashboard UI (live monitoring)

Use the built-in dashboard if you want a live, human-friendly view of process/activity:

1. Install dependencies:
   - `python3 -m pip install -r requirements.txt`
2. Start dashboard:
   - `streamlit run polymarket_dashboard.py`
3. In the UI:
   - click **Start Bot Loop** to run continuous trading loop
   - click **Stop Bot Loop** to halt it
   - watch **Live Process Log**, **Latest Cycle**, **Recent Trades**, and **Recent Orders**

The dashboard reads from:
- log file: `bot_runtime.log`
- state DB: `BOT_STATE_DB_PATH` (default `polymarket_bot_state.sqlite3`)

Dashboard diagnostics include:
- near-miss opportunities (closest non-qualifying candidates)
- cycle rejection counters (where candidates are filtered out)

## Trader activity tracker

Use this to analyze a public trader profile (for strategy inference from observed behavior):

- Run:
  - `python tools/trader_activity_tracker.py --user @sovereign2013`
- Output files (default directory `artifacts/trader_activity`):
  - `<user>_activity.csv`
  - `<user>_positions.csv`
  - `<user>_snapshot.json`
  - `<user>_report.md`

This gives you a reusable dataset/report so you can share one file instead of screenshots.

## Environment variables

### Strategy and market scanning
- `BOT_MODE`: `dry_run` (default) or `live`
- `BOT_SCAN_LIMIT`: markets per cycle (default `200`)
- `BOT_MIN_LIQUIDITY`: minimum liquidity (default `5000`)
- `BOT_MIN_EDGE`: minimum basket edge (default `0.01`)
- `BOT_MIN_PROFIT_USD`: minimum expected guaranteed profit (default `1.0`)
- `BOT_MAX_CAPITAL_PER_TRADE`: max USD deployed per opportunity (default `100`)
- `BOT_MAX_BUNDLE_SHARES`: max shares per basket (default `100`)
- `BOT_MIN_GROUP_SIZE`: min markets in grouped basket (default `4`)
- `BOT_MAX_GROUP_SIZE`: max markets in grouped basket (default `12`)
- `BOT_MAX_OPPS_PER_CYCLE`: max selected opportunities per cycle (default `3`)
- `BOT_MARKET_COOLDOWN_SECONDS`: market re-trade cooldown (default `20`)
- `BOT_SPORTS_ONLY`: `true`/`false` (default `true`)
- `BOT_INCLUDE_KEYWORDS`: optional comma-separated market text filter
- `BOT_ENABLE_NO_BASKET_STRATEGY`: enable cross-market NO basket arbitrage (`true` default)
- `BOT_ENABLE_BINARY_PAIR_STRATEGY`: enable YES+NO pair arbitrage inside one market (`true` default)
- `BOT_PAIR_MIN_EDGE`: minimum edge for pair strategy (default `0.001`)
- `BOT_PAIR_MIN_PROFIT_USD`: minimum expected profit for pair strategy (default `0.01`)

### Profit-first scoring and allocation
- `BOT_BANKROLL_USD`: bankroll used for allocation context (default `100`)
- `BOT_AGGRESSION`: 0..1 aggressiveness slider (default `0.5`)
- `BOT_MIN_NET_PROFIT_USD`: minimum estimated net profit after costs (default `0.25`)
- `BOT_MIN_NET_EDGE`: minimum estimated net edge after costs (default `0.0025`)
- `BOT_TAKER_FEE_RATE`: fee-rate coefficient for cost estimation (default `0.03`)
- `BOT_SLIPPAGE_BPS_BASE`: baseline slippage estimate in bps (default `5`)
- `BOT_SLIPPAGE_BPS_PER_LEG`: extra slippage bps per leg (default `2`)
- `BOT_SLIPPAGE_BPS_ILLIQUIDITY`: illiquidity penalty coefficient (default `4000`)
- `BOT_LATENCY_PENALTY_BPS`: latency cost estimate in bps (default `4`)
- `BOT_RISK_BUFFER_BPS`: additional safety margin in bps (default `8`)

### Auto-tune policy agent (live adaptive thresholds)
- `BOT_ENABLE_AUTO_TUNE`: enable adaptive tuning in loop (default `true`)
- `BOT_AUTO_TUNE_INTERVAL_CYCLES`: cycles between adjustments (default `3`)
- `BOT_AUTO_TUNE_AGGRESSION_STEP`: aggression step size per adjustment (default `0.03`)
- `BOT_AUTO_TUNE_PROFIT_STEP`: net-profit threshold step size (default `0.03`)
- `BOT_AUTO_TUNE_EDGE_STEP`: net-edge threshold step size (default `0.0004`)
- `BOT_AUTO_TUNE_MIN_AGGRESSION`: lower bound for aggression (default `0.55`)
- `BOT_AUTO_TUNE_MAX_AGGRESSION`: upper bound for aggression (default `0.97`)
- `BOT_AUTO_TUNE_MIN_NET_PROFIT_USD`: lower bound for min net profit (default `0.08`)
- `BOT_AUTO_TUNE_MAX_NET_PROFIT_USD`: upper bound for min net profit (default `0.75`)
- `BOT_AUTO_TUNE_MIN_NET_EDGE`: lower bound for min net edge (default `0.0012`)
- `BOT_AUTO_TUNE_MAX_NET_EDGE`: upper bound for min net edge (default `0.0100`)

### Optional external price check
- `BOT_ENABLE_EXTERNAL_PRICE_CHECK`: `true` to enable external consensus checks
- `BOT_ODDS_API_KEY`: The Odds API key (optional)
- `BOT_ODDS_REGIONS`: regions passed to external odds API (default `us,uk,eu`)
- `BOT_ODDS_MARKETS`: market types passed to external odds API (default `h2h`)

### Optional leader-follow directional mode
- `BOT_ENABLE_LEADER_FOLLOW`: enable directional entries from selected public trader flow (`false` default)
- `BOT_LEADER_WALLET`: public wallet address to follow (0x...)
- `BOT_LEADER_MAX_SIGNAL_AGE_SECONDS`: ignore stale signals older than this (default `180`)
- `BOT_LEADER_MIN_NOTIONAL_USD`: minimum observed trade size to consider (default `100`)
- `BOT_LEADER_PRICE_TOLERANCE_BPS`: max price drift vs observed leader fill (default `80`)
- `BOT_LEADER_ALPHA`: model edge uplift for selected leader signals (default `0.03`)
- `BOT_LEADER_MAX_SIGNALS_PER_CYCLE`: cap leader candidates per cycle (default `5`)
- `BOT_LEADER_REQUIRE_BUY_SIDE`: only follow BUY signals when `true` (default `true`)

### Data reliability and performance
- `BOT_REQUEST_TIMEOUT_SECONDS`: HTTP timeout (default `10`)
- `BOT_MAX_REQUEST_RETRIES`: retries for transient HTTP failures (default `3`)
- `BOT_RETRY_BACKOFF_SECONDS`: linear retry backoff base (default `0.35`)
- `BOT_MAX_QUOTE_FETCH_LATENCY_MS`: reject stale/slow quote fetches over threshold (default `1500`)
- `BOT_POLL_INTERVAL_SECONDS`: loop interval seconds (default `2`)

### Risk controls (live safety)
- `BOT_MAX_DAILY_LOSS_USD`: stop new trades after daily realized loss breach (default `250`)
- `BOT_MAX_OPEN_EXPOSURE_USD`: max total open exposure (default `1000`)
- `BOT_MAX_EVENT_EXPOSURE_USD`: max open exposure in one event group (default `300`)
- `BOT_MAX_ORDERS_PER_MINUTE`: order-rate safety cap (default `15`)
- `BOT_EMERGENCY_STOP`: `true` blocks all new executions (default `false`)

### State and logging
- `BOT_STATE_DB_PATH`: SQLite path (default `polymarket_bot_state.sqlite3`)
- `BOT_LOG_JSON`: emit JSON logs when `true` (default `true`)
- `BOT_ANALYSIS_LOG_PATH`: machine-readable per-cycle report log (default `bot_cycle_report.jsonl`)

### Live trading credentials
- `PM_PRIVATE_KEY`: required in live mode
- `PM_FUNDER`: required for signature types `1` or `2`
- `PM_CHAIN_ID`: default `137`
- `PM_SIGNATURE_TYPE`:
  - `0` = EOA (default in code)
  - `1` = POLY_PROXY
  - `2` = POLY_GNOSIS_SAFE
- Optional pre-provisioned API credentials:
  - `PM_API_KEY`
  - `PM_API_SECRET`
  - `PM_API_PASSPHRASE`

### Optional LLM ranking layer
- `BOT_ENABLE_LLM_RANKING`: `true` to enable ranking/filtering
- `BOT_LLM_PROVIDER`: metadata field for provider (`openai` default)
- `BOT_LLM_MODEL`: model name (`gpt-4o-mini` default)
- `BOT_LLM_API_KEY`: API key for ranking endpoint
- `BOT_LLM_ENDPOINT`: chat completions endpoint

## Go-live checklist

1. Start with `BOT_MODE=dry_run` and verify stable cycle logs.
2. Confirm risk settings are intentionally conservative.
3. Run `pytest` and resolve all failures before live mode.
4. Verify `BOT_EMERGENCY_STOP` toggles blocking behavior as expected.
5. Run `--once` in live mode with tiny `BOT_MAX_CAPITAL_PER_TRADE`.
6. Confirm orders persist in SQLite (`trades` and `orders` tables).
7. Keep continuous monitoring and be prepared to toggle emergency stop.

## Important risk note

This software is experimental and can lose money. Exchange behavior, latency, and partial fills can create real exposure. Use small limits first and operate with strict safeguards.