# Polymarket Bot Handoff Issues

Last updated: 2026-04-05  
Project root: `C:\Users\ryano\Downloads\polymart`

This document lists current and foreseeable issues that block reliability/profitability, with evidence and likely root causes for fast handoff.

## Critical

### 1) Bot cycles are frequently interrupted before completion
- **Symptom:** Cycle counter appears "stuck" and no new completed cycles are recorded.
- **Evidence:** Repeated `KeyboardInterrupt` traces in `bot_runtime.log` after dashboard starts.
- **Impact:** No `record_cycle` completion, no new metrics, no valid operational signal.
- **Likely root causes:**
  - Run interruptions (terminal/process control events).
  - Long cycle duration increases chance of manual interruption.
- **Files:** `polymarket_bot/main.py`, `polymarket_bot/runtime.py`, `polymarket_dashboard.py`.

### 2) Scan cycle latency is too high for stable operations
- **Symptom:** Single cycles take ~85s to ~255s at `BOT_SCAN_LIMIT=400`.
- **Evidence:** `stage_diagnostics.scanner_ms` in `bot_cycle_report.jsonl`.
- **Impact:** Users perceive lockup and restart repeatedly; runtime churn.
- **Likely root causes:**
  - High request volume with sequential quote fetches.
  - Tight timeout/backoff settings under network variability.
- **Files:** `polymarket_bot/scanner.py`, `polymarket_bot/data_client.py`.

### 3) Runtime loop can terminate on unhandled network exceptions
- **Symptom:** Full bot loop exits after request failures.
- **Evidence:** Exception stack traces bubble from `_request_json()` to loop context.
- **Impact:** Bot downtime; dashboard shows stale status.
- **Likely root causes:** Missing per-cycle top-level exception guard inside `run_bot_loop()`.
- **Files:** `polymarket_bot/runtime.py`, `polymarket_bot/data_client.py`.

### 4) Secret-management risk: private key in plaintext `.env`
- **Symptom:** `PM_PRIVATE_KEY` stored directly in repo-local config.
- **Impact:** Wallet compromise risk if leaked.
- **Action required:** Rotate key; move secret to secure store or environment injection.
- **Files:** `.env`.

## High

### 5) Leader-follow strategy is mostly inactive (`signal_market_miss`)
- **Symptom:** Leader signals pass filters but almost always fail mapping to active market universe.
- **Evidence:** `leader_diagnostics`: `signals_after_filters > 0` and `signal_market_miss ~= signals_after_filters`, `opportunities_built=0`.
- **Impact:** Expected alpha source contributes no candidates.
- **Likely root causes:**
  - Slug/token mapping mismatch window.
  - Market universe load timing and activity feed timing mismatch.
- **Files:** `polymarket_bot/leader_follow.py`, `polymarket_bot/runtime.py`.

### 6) Current strategy regime produces persistently negative edge
- **Symptom:** No-basket and pair best edges often deeply negative.
- **Evidence:** Diagnostics fields like `no_basket_best_edge_ppm ~ -940000`.
- **Impact:** `selected_candidates=0` is expected; no simulated/live entries.
- **Likely root causes:** Market regime not favorable to arbitrage formulas.
- **Files:** `polymarket_bot/scanner.py`, `bot_cycle_report.jsonl`.

### 7) Reconciliation records assumed fills in success path
- **Symptom:** Successful execution path records a fill immediately with expected cost.
- **Evidence:** `reconcile_trade()` writes `state.record_fill(... filled_usd=opp.total_cost_usd)` without exchange callback confirmation.
- **Impact:** PnL/exposure can diverge from reality in live trading.
- **Files:** `polymarket_bot/reconcile.py`, `polymarket_bot/state.py`.

### 8) Dry-run writes simulated orders into production order table
- **Symptom:** `DryRunExecutor` persists simulated orders in `orders`.
- **Impact:** Dashboard/order stats mix simulation with live-like records.
- **Files:** `polymarket_bot/execution.py`, `polymarket_bot/state.py`.

## Medium

### 9) Dashboard start/stop UX has historically allowed operational confusion
- **Symptom:** Multiple starts and intermittent server disconnect behavior.
- **Impact:** Duplicate process attempts, stale UI state perception.
- **Files:** `polymarket_dashboard.py`.

### 10) Browser-side Vega/Streamlit warnings
- **Symptom:** Vega-Lite version warnings, `Infinite extent`, scale-binding warnings.
- **Impact:** Console noise; possible chart degradation for empty/discrete data.
- **Files:** `polymarket_dashboard.py` chart sections.

### 11) Log/report growth is unbounded
- **Symptom:** `bot_runtime.log` and `bot_cycle_report.jsonl` continuously append.
- **Impact:** Performance degradation and harder diagnosis over time.
- **Files:** `bot_runtime.log`, `bot_cycle_report.jsonl`.

## Foreseeable Issues

### 12) Throughput bottleneck as strategy breadth increases
- **Risk:** Enabling all strategies with high scan limits increases request load and latency.
- **Needed:** Request budget control per cycle and adaptive throttling.

### 13) Data-schema drift risk (external APIs)
- **Risk:** Activity/market payload field changes can silently reduce leader-follow effectiveness.
- **Needed:** Schema validation + explicit alerting on parse/mapping failures.

### 14) No robust profitability calibration layer yet
- **Risk:** Directional mode can remain active without demonstrable long-run edge quality.
- **Needed:** CLV-style tracking, calibration reports, strategy-level stop/go gating.

## Immediate Stabilization Checklist (for next engineer/agent)

1. Add top-level try/except in `run_bot_loop()` that logs and continues per cycle (with bounded retry backoff).
2. Reduce cycle request footprint or parallelize quote fetch path safely.
3. Separate dry-run and live persistence paths (`orders` / `fills`) or clearly label simulation records.
4. Fix leader-follow mapping robustness (token-first mapping with fallback joins and diagnostics).
5. Add log rotation and report retention policy.
6. Rotate `PM_PRIVATE_KEY` and remove plaintext secret handling from local handoff flow.

## Known Runtime Snapshot (as observed)

- Mode observed during diagnostics: `dry_run`.
- Strategies observed in report:
  - `no_basket=true`
  - `pair=true`
  - `multi_outcome=true`
  - `leader_follow=true`
- Typical cycle diagnostics:
  - `scanner_candidates=0`
  - `leader_candidates=0`
  - `selected_candidates=0`
  - `leader_diagnostics.signal_market_miss` frequently high.

## Primary files to inspect first

- `polymarket_bot/runtime.py`
- `polymarket_bot/data_client.py`
- `polymarket_bot/scanner.py`
- `polymarket_bot/leader_follow.py`
- `polymarket_bot/execution.py`
- `polymarket_bot/reconcile.py`
- `polymarket_dashboard.py`
- `.env`
- `bot_runtime.log`
- `bot_cycle_report.jsonl`
