from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st


APP_TITLE = "Polymarket Bot Control Center"
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / "bot_runtime.log"
DEFAULT_DB_PATH = os.getenv("BOT_STATE_DB_PATH", "polymarket_bot_state.sqlite3")
DB_PATH = (PROJECT_ROOT / DEFAULT_DB_PATH).resolve()
DEFAULT_ANALYSIS_LOG_PATH = os.getenv("BOT_ANALYSIS_LOG_PATH", "bot_cycle_report.jsonl")
ANALYSIS_LOG_PATH = (PROJECT_ROOT / DEFAULT_ANALYSIS_LOG_PATH).resolve()
ENV_PATH = PROJECT_ROOT / ".env"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_state() -> None:
    if "bot_proc" not in st.session_state:
        st.session_state.bot_proc = None
    if "started_at" not in st.session_state:
        st.session_state.started_at = None
    if "ui_notice" not in st.session_state:
        st.session_state.ui_notice = ""


def _is_running(proc: subprocess.Popen[str] | None) -> bool:
    return proc is not None and proc.poll() is None


def _start_bot() -> None:
    if _is_running(st.session_state.bot_proc):
        st.session_state.ui_notice = "Bot is already running in this dashboard session."
        return

    env = os.environ.copy()
    
    # Basic log maintenance: Prevent unbounded growth on start (Issue #11)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 10 * 1024 * 1024: # 10MB limit
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"[{_now_utc()}] Log rotated by dashboard (exceeded 10MB)\n")

    cmd = [sys.executable, "-m", "polymarket_bot.main"]
    log_file = open(LOG_PATH, "a", encoding="utf-8")
    log_file.write(f"\n[{_now_utc()}] Starting bot from dashboard\n")
    log_file.flush()

    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            if os.name == "nt"
            else 0
        ),
    )
    st.session_state.bot_proc = proc
    st.session_state.started_at = time.time()
    st.session_state.ui_notice = f"Started bot process PID {proc.pid}."


def _stop_bot() -> None:
    proc: subprocess.Popen[str] | None = st.session_state.bot_proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    st.session_state.bot_proc = None
    st.session_state.started_at = None
    st.session_state.ui_notice = "Stopped bot process."
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"[{_now_utc()}] Stopped bot from dashboard\n")


def _db_connect() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_one(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    try:
        return conn.execute(query, params).fetchone()
    except sqlite3.OperationalError:
        return None


def _fetch_all(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _load_dashboard_data() -> dict[str, Any]:
    data: dict[str, Any] = {
        "open_exposure": 0.0,
        "daily_pnl": 0.0,
        "last_cycle": None,
        "total_cycles": 0,
        "total_trades": 0,
        "total_orders": 0,
        "recent_trades": [],
        "recent_orders": [],
        "cycle_history": [],
        "pnl_history": [],
        "order_status_counts": {},
        "error_orders_count": 0,
        "simulated_pnl": 0.0,
        "simulated_performance": [],
        "latest_near_misses": [],
        "latest_cycle_diagnostics": {},
    }
    conn = _db_connect()
    if conn is None:
        return data
    try:
        open_row = _fetch_one(
            conn,
            "SELECT COALESCE(SUM(cost_usd), 0) AS open_exposure FROM trades WHERE status IN ('submitted', 'partially_filled')",
        )
        today = datetime.now(timezone.utc).date().isoformat()
        pnl_row = _fetch_one(
            conn,
            "SELECT COALESCE(SUM(realized_pnl_usd), 0) AS pnl FROM fills WHERE substr(created_at, 1, 10) = ?",
            (today,),
        )
        cycle = _fetch_one(
            conn,
            "SELECT cycle_id, started_at, scanned_markets, opportunities, executed FROM cycles ORDER BY started_at DESC LIMIT 1",
        )
        trades_count = _fetch_one(conn, "SELECT COUNT(*) AS c FROM trades")
        orders_count = _fetch_one(conn, "SELECT COUNT(*) AS c FROM orders")
        cycles_count = _fetch_one(conn, "SELECT COUNT(*) AS c FROM cycles")
        recent_trades = _fetch_all(
            conn,
            """
            SELECT trade_id, group_key, mode, status, cost_usd, expected_profit_usd, created_at
            FROM trades
            ORDER BY created_at DESC
            LIMIT 12
            """,
        )
        recent_orders = _fetch_all(
            conn,
            """
            SELECT trade_id, market_slug, token_id, mode, status, order_id, error, created_at
            FROM orders
            ORDER BY created_at DESC
            LIMIT 20
            """,
        )
        cycle_history = _fetch_all(
            conn,
            """
            SELECT started_at, scanned_markets, opportunities, executed
            FROM cycles
            ORDER BY started_at DESC
            LIMIT 80
            """,
        )
        pnl_history = _fetch_all(
            conn,
            """
            SELECT created_at, realized_pnl_usd
            FROM fills
            ORDER BY created_at ASC
            LIMIT 500
            """,
        )
        order_status = _fetch_all(
            conn,
            """
            SELECT status, COUNT(*) AS c
            FROM orders
            GROUP BY status
            ORDER BY c DESC
            """,
        )
        error_count = _fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM orders WHERE error IS NOT NULL OR status IN ('failed', 'canceled')",
        )
        near_misses = _fetch_all(
            conn,
            """
            SELECT nm.rank_index, nm.group_key, nm.legs_considered, nm.sum_ask, nm.payout_per_share,
                   nm.edge, nm.min_edge_required, nm.edge_gap, nm.estimated_profit_usd,
                   nm.min_profit_required, nm.profit_gap_usd, nm.created_at
            FROM near_misses nm
            INNER JOIN (
                SELECT cycle_id
                FROM cycles
                ORDER BY started_at DESC
                LIMIT 1
            ) lc ON lc.cycle_id = nm.cycle_id
            ORDER BY nm.rank_index ASC
            LIMIT 8
            """,
        )
        cycle_diagnostics = _fetch_one(
            conn,
            """
            SELECT cd.diagnostics_json
            FROM cycle_diagnostics cd
            INNER JOIN (
                SELECT cycle_id
                FROM cycles
                ORDER BY started_at DESC
                LIMIT 1
            ) lc ON lc.cycle_id = cd.cycle_id
            LIMIT 1
            """,
        )
        sim_pnl_row = _fetch_one(
            conn,
            """
            SELECT COALESCE(SUM((current_sum_ask - entry_sum_ask) * shares), 0) AS unrealized_pnl 
            FROM simulated_performance
            """
        )
        sim_perf = _fetch_all(
            conn,
            """
            SELECT trade_id, group_key, entry_sum_ask, current_sum_ask, (current_sum_ask - entry_sum_ask) * shares AS pnl, updated_at
            FROM simulated_performance
            ORDER BY updated_at DESC
            LIMIT 10
            """
        )

        data["open_exposure"] = float(open_row["open_exposure"]) if open_row else 0.0
        data["daily_pnl"] = float(pnl_row["pnl"]) if pnl_row else 0.0
        data["last_cycle"] = dict(cycle) if cycle else None
        data["total_cycles"] = int(cycles_count["c"]) if cycles_count else 0
        data["total_trades"] = int(trades_count["c"]) if trades_count else 0
        data["total_orders"] = int(orders_count["c"]) if orders_count else 0
        data["recent_trades"] = [dict(r) for r in recent_trades]
        data["recent_orders"] = [dict(r) for r in recent_orders]
        data["simulated_performance"] = [dict(r) for r in sim_perf]
        data["simulated_pnl"] = float(sim_pnl_row["unrealized_pnl"]) if sim_pnl_row else 0.0
        data["cycle_history"] = [dict(r) for r in reversed(cycle_history)]
        data["pnl_history"] = [dict(r) for r in pnl_history]
        data["order_status_counts"] = {str(r["status"]): int(r["c"]) for r in order_status}
        data["error_orders_count"] = int(error_count["c"]) if error_count else 0
        data["latest_near_misses"] = [dict(r) for r in near_misses]
        if cycle_diagnostics and cycle_diagnostics["diagnostics_json"]:
            try:
                data["latest_cycle_diagnostics"] = json.loads(str(cycle_diagnostics["diagnostics_json"]))
            except json.JSONDecodeError:
                data["latest_cycle_diagnostics"] = {}
        return data
    finally:
        conn.close()


def _read_log_tail(lines: int = 120) -> str:
    if not LOG_PATH.exists():
        return "No runtime log yet. Start the bot to stream logs."
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.readlines()
    tail = "".join(content[-lines:])
    return tail if tail.strip() else "Log file exists but is currently empty."


def _parse_dotenv_non_secret() -> dict[str, str]:
    watched = {
        "BOT_MODE",
        "BOT_SCAN_LIMIT",
        "BOT_SPORTS_ONLY",
        "BOT_MIN_LIQUIDITY",
        "BOT_MIN_EDGE",
        "BOT_MIN_PROFIT_USD",
        "BOT_MIN_GROUP_SIZE",
        "BOT_MAX_GROUP_SIZE",
        "BOT_MAX_CAPITAL_PER_TRADE",
        "BOT_MAX_BUNDLE_SHARES",
        "BOT_MAX_OPPS_PER_CYCLE",
        "BOT_MAX_OPEN_EXPOSURE_USD",
        "BOT_MAX_EVENT_EXPOSURE_USD",
        "BOT_MAX_DAILY_LOSS_USD",
        "BOT_MAX_ORDERS_PER_MINUTE",
        "BOT_MARKET_COOLDOWN_SECONDS",
        "BOT_EMERGENCY_STOP",
        "BOT_ENABLE_NO_BASKET_STRATEGY",
        "BOT_ENABLE_BINARY_PAIR_STRATEGY",
        "BOT_ENABLE_MULTI_OUTCOME_STRATEGY",
        "BOT_ENABLE_LEADER_FOLLOW",
        "BOT_PAIR_MIN_EDGE",
        "BOT_PAIR_MIN_PROFIT_USD",
        "BOT_MULTI_MIN_EDGE",
        "BOT_MULTI_MIN_PROFIT_USD",
        "BOT_REQUEST_TIMEOUT_SECONDS",
        "BOT_MAX_REQUEST_RETRIES",
        "BOT_RETRY_BACKOFF_SECONDS",
        "BOT_MAX_QUOTE_FETCH_LATENCY_MS",
        "BOT_POLL_INTERVAL_SECONDS",
        "PM_CHAIN_ID",
        "PM_SIGNATURE_TYPE",
    }
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in watched:
            continue
        out[key] = value.strip()
    return out


def _set_env_key(key: str, value: str) -> bool:
    if not ENV_PATH.exists():
        return False
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        existing_key, _existing_value = line.split("=", 1)
        if existing_key.strip() == key:
            lines[idx] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _extract_startup_runtime_values() -> dict[str, str]:
    if not LOG_PATH.exists():
        return {}
    startup_line = ""
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Starting polymarket bot mode=" in line:
                startup_line = line.strip()
    if not startup_line:
        return {}

    parsed: dict[str, str] = {"raw": startup_line}
    if startup_line.startswith("{") and startup_line.endswith("}"):
        try:
            payload = json.loads(startup_line)
            startup_line = str(payload.get("message", startup_line))
        except json.JSONDecodeError:
            pass

    marker = "Starting polymarket bot "
    idx = startup_line.find(marker)
    if idx >= 0:
        chunk = startup_line[idx + len(marker) :]
    else:
        chunk = startup_line
    for token in chunk.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parsed[key.strip()] = value.strip().strip(",")
    return parsed


def _load_latest_cycle_report() -> dict[str, Any] | None:
    if not ANALYSIS_LOG_PATH.exists():
        return None
    lines = ANALYSIS_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _runtime_integrity_checks(
    running: bool,
    env_values: dict[str, str],
    startup_values: dict[str, str],
    latest_report: dict[str, Any] | None,
) -> tuple[bool, list[tuple[bool, str]]]:
    checks: list[tuple[bool, str]] = []

    mode_expected = env_values.get("BOT_MODE", "").strip().lower()
    mode_actual = startup_values.get("mode", "").strip().lower()
    checks.append((bool(mode_expected) and mode_expected == mode_actual, f"Startup mode matches .env ({mode_expected})."))

    if latest_report is None:
        checks.append((False, "Latest cycle report exists."))
        return False, checks
    checks.append((True, "Latest cycle report exists."))

    report_mode = str(latest_report.get("mode", "")).strip().lower()
    checks.append((report_mode == mode_actual, "Cycle report mode matches startup mode."))

    strategies = latest_report.get("strategies", {})
    leader_enabled = str(env_values.get("BOT_ENABLE_LEADER_FOLLOW", "false")).strip().lower() == "true"
    if isinstance(strategies, dict):
        checks.append((bool(strategies.get("leader_follow", False)) == leader_enabled, "Leader-follow strategy flag is correct."))
    else:
        checks.append((False, "Leader-follow strategy flag is present."))

    stage_diag = latest_report.get("stage_diagnostics", {})
    leader_diag = latest_report.get("leader_diagnostics", {})
    checks.append((isinstance(stage_diag, dict) and len(stage_diag) > 0, "Stage diagnostics are present."))
    checks.append((isinstance(leader_diag, dict) and len(leader_diag) > 0, "Leader diagnostics are present."))

    summary = latest_report.get("summary", {})
    cycle_completed = isinstance(summary, dict) and "scanned_markets" in summary and "selected_opportunities" in summary
    checks.append((cycle_completed, "At least one full cycle summary was recorded."))

    if running:
        checks.append((True, "Bot process is running in this dashboard session."))
    else:
        checks.append((False, "Bot process is running in this dashboard session."))

    passed = all(ok for ok, _msg in checks)
    return passed, checks


def _parameter_check_rows(expected: dict[str, str], actual: dict[str, str]) -> list[tuple[str, bool, str]]:
    mapping = [
        ("BOT_MODE", "mode"),
        ("BOT_SCAN_LIMIT", "scan_limit"),
        ("BOT_MIN_GROUP_SIZE", "min_group"),
        ("BOT_MAX_GROUP_SIZE", "max_group"),
        ("BOT_ENABLE_NO_BASKET_STRATEGY", "no_basket"),
        ("BOT_ENABLE_BINARY_PAIR_STRATEGY", "pair"),
        ("BOT_ENABLE_MULTI_OUTCOME_STRATEGY", "multi"),
    ]
    rows: list[tuple[str, bool, str]] = []
    for env_key, actual_key in mapping:
        exp = expected.get(env_key)
        got = actual.get(actual_key)
        if exp is None or got is None:
            rows.append((env_key, False, "missing"))
            continue
        ok = exp.strip().lower() == got.strip().lower()
        rows.append((env_key, ok, f"expected={exp} actual={got}"))
    return rows


def _compute_outlook(data: dict[str, Any], latest_report: dict[str, Any] | None) -> tuple[str, str, str]:
    diagnostics = data.get("latest_cycle_diagnostics", {}) or {}
    stage = latest_report.get("stage_diagnostics", {}) if isinstance(latest_report, dict) else {}
    selected = int(stage.get("selected_candidates", 0)) if isinstance(stage, dict) else 0
    merged = int(stage.get("merged_candidates", 0)) if isinstance(stage, dict) else 0

    edges = []
    for key in ("no_basket_best_edge_ppm", "pair_best_edge_ppm", "multi_best_edge_ppm"):
        value = diagnostics.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed <= -999000:
            continue
        edges.append(parsed / 1_000_000.0)

    max_edge = max(edges) if edges else -1.0
    if selected > 0:
        return "TRADE-READY", f"selected={selected}, merged={merged}, best_edge={max_edge:.4f}", "success"
    if merged > 0 and max_edge > -0.02:
        return "NEAR BREAK-EVEN", f"merged={merged}, selected=0, best_edge={max_edge:.4f}", "warning"
    if max_edge >= 0:
        return "POSITIVE SIGNALS", f"selected=0, best_edge={max_edge:.4f}", "info"
    return "NEGATIVE EDGE REGIME", f"merged={merged}, selected=0, best_edge={max_edge:.4f}", "error"


def _format_status_text(data: dict[str, Any], running: bool) -> str:
    last_cycle = data["last_cycle"]
    if last_cycle is None:
        return "No cycle has completed yet. This is normal right after setup."
    if running and int(last_cycle["executed"]) == 0:
        return (
            "Bot is running and scanning. No trades executed in latest cycle, which usually means "
            "no market met your edge/profit rules at that moment."
        )
    if int(last_cycle["executed"]) > 0:
        return "Latest cycle executed trades. Review orders and exposure below."
    return "Bot is connected and cycles are completing."


def _json_to_readable(log_blob: str) -> str:
    lines = []
    for raw in log_blob.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("{") and raw.endswith("}"):
            try:
                obj = json.loads(raw)
                ts = obj.get("ts", "")
                level = obj.get("level", "")
                msg = obj.get("message", raw)
                lines.append(f"{ts} [{level}] {msg}")
                continue
            except json.JSONDecodeError:
                pass
        lines.append(raw)
    return "\n".join(lines)


def _build_alerts(data: dict[str, Any], running: bool, readable_log: str) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    max_open_exposure = _env_float("BOT_MAX_OPEN_EXPOSURE_USD", 1000.0)
    max_daily_loss = abs(_env_float("BOT_MAX_DAILY_LOSS_USD", 250.0))
    open_exposure = float(data["open_exposure"])
    daily_pnl = float(data["daily_pnl"])

    if not running:
        alerts.append(("warning", "Bot process is not running. No new scans or trades are happening."))
    if data["last_cycle"] is None:
        alerts.append(("info", "No completed cycle yet. Run one cycle or start the loop to initialize activity."))

    if max_open_exposure > 0 and (open_exposure / max_open_exposure) >= 0.8:
        alerts.append(
            (
                "warning",
                f"Open exposure is above 80% of your configured cap (${open_exposure:.2f}/${max_open_exposure:.2f}).",
            )
        )
    if max_daily_loss > 0 and daily_pnl <= -0.8 * max_daily_loss:
        alerts.append(
            (
                "error",
                f"Daily realized PnL is near/over your loss limit (${daily_pnl:.2f} vs -${max_daily_loss:.2f}).",
            )
        )

    if int(data.get("error_orders_count", 0)) > 0:
        alerts.append(("error", f"{data['error_orders_count']} order(s) show failed/canceled/error states."))

    log_tail_lower = readable_log.lower()
    if "live-error" in log_tail_lower or "error" in log_tail_lower:
        alerts.append(("warning", "Recent logs include errors. Review the Live Process Log section."))

    if not alerts:
        alerts.append(("success", "System looks healthy. Data, state, and process flow appear normal."))
    return alerts


def _risk_utilization(data: dict[str, Any]) -> dict[str, float]:
    max_open_exposure = max(_env_float("BOT_MAX_OPEN_EXPOSURE_USD", 1000.0), 1e-9)
    max_daily_loss = max(abs(_env_float("BOT_MAX_DAILY_LOSS_USD", 250.0)), 1e-9)
    open_ratio = min(max(float(data["open_exposure"]) / max_open_exposure, 0.0), 1.0)
    loss_ratio = min(max((-float(data["daily_pnl"])) / max_daily_loss, 0.0), 1.0)
    return {
        "open_ratio": open_ratio,
        "loss_ratio": loss_ratio,
        "max_open_exposure": max_open_exposure,
        "max_daily_loss": max_daily_loss,
    }


def _pnl_timeseries(data: dict[str, Any]) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    running = 0.0
    for row in data["pnl_history"]:
        running += float(row["realized_pnl_usd"])
        out.append({"time": str(row["created_at"]), "cumulative_pnl_usd": running})
    return out


def _near_miss_reason(row: dict[str, Any]) -> str:
    edge_gap = float(row["edge_gap"])
    profit_gap = float(row["profit_gap_usd"])
    if edge_gap > 0 and profit_gap > 0:
        return (
            f"Needs +{edge_gap:.4f} edge and +${profit_gap:.4f} more estimated profit "
            "to pass current rules."
        )
    if edge_gap > 0:
        return f"Edge short by {edge_gap:.4f} versus threshold."
    if profit_gap > 0:
        return f"Profit short by ${profit_gap:.4f} versus threshold."
    return "Very close candidate; failed by another filter or ranking cutoff."


def _diagnostic_float(diag: dict[str, Any], key: str, scale: float) -> float | None:
    value = diag.get(key)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed / scale


def _signal_text(value: float | None) -> str:
    if value is None or value <= -999.0:
        return "n/a"
    return f"{value:.4f}"


def _runtime_mismatch(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    checks = {
        "BOT_MODE": "mode",
        "BOT_SCAN_LIMIT": "scan_limit",
        "BOT_SPORTS_ONLY": "sports_only",
        "BOT_MIN_GROUP_SIZE": "min_group",
        "BOT_MAX_GROUP_SIZE": "max_group",
        "BOT_ENABLE_NO_BASKET_STRATEGY": "no_basket",
        "BOT_ENABLE_BINARY_PAIR_STRATEGY": "pair",
        "BOT_ENABLE_MULTI_OUTCOME_STRATEGY": "multi",
    }
    mismatch: list[str] = []
    for env_key, actual_key in checks.items():
        exp = expected.get(env_key)
        got = actual.get(actual_key)
        if exp is None or got is None:
            continue
        if exp.strip().lower() != got.strip().lower():
            mismatch.append(f"{env_key} expected={exp} actual={got}")
    return mismatch


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    _init_state()
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
        h1, h2, h3 {margin-top: 0.2rem;}
        .stMetric {padding: 0.1rem 0.2rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title(APP_TITLE)
    st.caption("Compact monitoring and control for your trading bot.")

    proc: subprocess.Popen[str] | None = st.session_state.bot_proc
    running = _is_running(proc)

    env_values = _parse_dotenv_non_secret()
    startup_values = _extract_startup_runtime_values()
    latest_report = _load_latest_cycle_report()
    integrity_ok, integrity_checks = _runtime_integrity_checks(running, env_values, startup_values, latest_report)

    with st.sidebar:
        st.subheader("Control Panel")
        if running:
            st.success(f"Running (PID {proc.pid})")
        else:
            st.warning("Stopped")
        if st.button("Start Bot Loop", disabled=running, width="stretch"):
            _start_bot()
            st.rerun()
        confirm_stop = st.checkbox("Confirm stop actions", value=False)
        if st.button("Stop Bot Loop", disabled=(not running or not confirm_stop), width="stretch"):
            _stop_bot()
            st.rerun()
        if st.button("EMERGENCY STOP", disabled=not confirm_stop, width="stretch"):
            env_ok = _set_env_key("BOT_EMERGENCY_STOP", "true")
            _stop_bot()
            st.session_state.ui_notice = (
                "Emergency stop activated. Process halted and BOT_EMERGENCY_STOP=true."
                if env_ok
                else "Emergency stop activated, but .env update failed."
            )
            st.rerun()
        if st.button("Clear Emergency Stop", width="stretch"):
            env_ok = _set_env_key("BOT_EMERGENCY_STOP", "false")
            st.session_state.ui_notice = "BOT_EMERGENCY_STOP=false saved." if env_ok else "Could not update .env."
            st.rerun()

        if st.session_state.ui_notice:
            st.info(st.session_state.ui_notice)

        if st.session_state.started_at:
            uptime_sec = int(time.time() - st.session_state.started_at)
            st.caption(f"Session uptime: {uptime_sec}s")

        with st.expander("Runtime Parameter Menu", expanded=False):
            rows = _parameter_check_rows(env_values, startup_values)
            ok_count = sum(1 for _k, ok, _d in rows if ok)
            st.write(f"{ok_count}/{len(rows)} matched")
            for key, ok, detail in rows:
                st.write(f"{'✅' if ok else '❌'} `{key}` - {detail}")
            st.caption(f"Log: {LOG_PATH.name} | DB: {DB_PATH.name} | Report: {ANALYSIS_LOG_PATH.name}")

        with st.expander("Runtime Integrity", expanded=False):
            st.write("PASS" if integrity_ok else "FAIL")
            for ok, msg in integrity_checks:
                st.write(f"{'✅' if ok else '❌'} {msg}")

        with st.expander("Config JSON (optional)", expanded=False):
            st.markdown("**Expected (.env)**")
            st.json(env_values if env_values else {})
            st.markdown("**Actual startup**")
            st.json(startup_values if startup_values else {})

    data = _load_dashboard_data()
    st.subheader("What Is Happening")
    st.write(_format_status_text(data, running))

    tail = _read_log_tail(lines=120)
    readable = _json_to_readable(tail)
    alerts = _build_alerts(data, running, readable)
    st.subheader("Alert Center")
    for level, message in alerts:
        if level == "error":
            st.error(message)
        elif level == "warning":
            st.warning(message)
        elif level == "success":
            st.success(message)
        else:
            st.info(message)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Open Exposure (USD)", f"{data['open_exposure']:.2f}")
    m2.metric("Daily Realized PnL (USD)", f"{data['daily_pnl']:.2f}")
    m3.metric("Simulated MtM PnL (USD)", f"{data['simulated_pnl']:.2f}")
    m4.metric("Total Trades Recorded", f"{data['total_trades']}")
    m5.metric("Total Cycles", f"{data['total_cycles']}")

    outlook_label, outlook_detail, outlook_level = _compute_outlook(data, latest_report)
    st.subheader("Live Outlook")
    if outlook_level == "success":
        st.success(f"{outlook_label} - {outlook_detail}")
    elif outlook_level == "warning":
        st.warning(f"{outlook_label} - {outlook_detail}")
    elif outlook_level == "error":
        st.error(f"{outlook_label} - {outlook_detail}")
    else:
        st.info(f"{outlook_label} - {outlook_detail}")

    tab_overview, tab_diagnostics, tab_logs = st.tabs(["Overview", "Diagnostics", "Logs"])

    with tab_overview:
        st.subheader("Risk Gauge")
        risk = _risk_utilization(data)
        st.progress(
            risk["open_ratio"],
            text=f"Open Exposure Usage: {risk['open_ratio'] * 100:.1f}% of cap (${risk['max_open_exposure']:.2f})",
        )
        st.progress(
            risk["loss_ratio"],
            text=f"Daily Loss Usage: {risk['loss_ratio'] * 100:.1f}% of cap (${risk['max_daily_loss']:.2f})",
        )

        trend_left, trend_right = st.columns(2)
        with trend_left:
            st.subheader("Cycle Throughput Trend")
            cycle_history = data["cycle_history"]
            if cycle_history:
                cycle_chart = [
                    {
                        "started_at": row["started_at"],
                        "executed": row["executed"],
                        "opportunities": row["opportunities"],
                        "scanned_markets": row["scanned_markets"],
                    }
                    for row in cycle_history
                ]
                st.line_chart(cycle_chart, x="started_at", y=["scanned_markets", "opportunities", "executed"], height=220)
            else:
                st.write("No cycle history yet.")

        with trend_right:
            st.subheader("PnL Trend")
            pnl_points = _pnl_timeseries(data)
            if pnl_points:
                st.line_chart(pnl_points, x="time", y="cumulative_pnl_usd", height=220)
            else:
                st.write("No fills yet, so no PnL trend is available.")

        st.subheader("Order Status Breakdown")
        if data["order_status_counts"]:
            status_rows = [{"status": k, "count": v} for k, v in data["order_status_counts"].items()]
            st.bar_chart(status_rows, x="status", y="count")
        else:
            st.write("No orders recorded yet.")

    with tab_diagnostics:
        st.subheader("Near-Miss Diagnostics (Latest Cycle)")
        near_misses = data["latest_near_misses"]
        if near_misses:
            rows = []
            for row in near_misses:
                formatted = dict(row)
                formatted["reason"] = _near_miss_reason(row)
                rows.append(formatted)
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.write("No near-miss diagnostics yet. Let at least one full cycle complete.")

        st.subheader("Cycle Rejection Reasons")
        diagnostics = data.get("latest_cycle_diagnostics", {})
        if diagnostics:
            signal_left, signal_right = st.columns(2)
            with signal_left:
                st.markdown("#### Strategy Signal Window")
                no_basket_best_edge = _diagnostic_float(diagnostics, "no_basket_best_edge_ppm", 1_000_000.0)
                no_basket_best_profit = _diagnostic_float(diagnostics, "no_basket_best_profit_cents", 100.0)
                pair_best_edge = _diagnostic_float(diagnostics, "pair_best_edge_ppm", 1_000_000.0)
                pair_best_profit = _diagnostic_float(diagnostics, "pair_best_profit_cents", 100.0)
                multi_best_edge = _diagnostic_float(diagnostics, "multi_best_edge_ppm", 1_000_000.0)
                multi_best_profit = _diagnostic_float(diagnostics, "multi_best_profit_cents", 100.0)

                c1, c2 = st.columns(2)
                c1.metric("Best NO-Basket Edge", _signal_text(no_basket_best_edge))
                c2.metric("Best NO-Basket Profit (USD)", _signal_text(no_basket_best_profit))
                c3, c4 = st.columns(2)
                c3.metric("Best Pair Edge", _signal_text(pair_best_edge))
                c4.metric("Best Pair Profit (USD)", _signal_text(pair_best_profit))
                c5, c6 = st.columns(2)
                c5.metric("Best Multi-Outcome Edge", _signal_text(multi_best_edge))
                c6.metric("Best Multi-Outcome Profit (USD)", _signal_text(multi_best_profit))

            with signal_right:
                st.markdown("#### Raw Cycle Counters")
                counters_only = {
                    k: v
                    for k, v in diagnostics.items()
                    if not str(k).endswith("_ppm") and not str(k).endswith("_cents")
                }
                st.json(counters_only)
        else:
            st.write("No cycle diagnostics recorded yet.")

        st.subheader("Simulated Performance Tracking (Issue #14)")
        if data["simulated_performance"]:
            st.dataframe(data["simulated_performance"], width="stretch", hide_index=True)
        else:
            st.write("No simulated trades tracked yet.")

        tcol, ocol = st.columns(2)
        with tcol:
            st.subheader("Recent Trades")
            if data["recent_trades"]:
                st.dataframe(data["recent_trades"], width="stretch", hide_index=True)
            else:
                st.write("No trades recorded yet.")
        with ocol:
            st.subheader("Recent Orders")
            if data["recent_orders"]:
                st.dataframe(data["recent_orders"], width="stretch", hide_index=True)
            else:
                st.write("No orders recorded yet.")

    with tab_logs:
        st.subheader("Latest Cycle")
        if data["last_cycle"] is None:
            st.write("No completed cycle yet.")
        else:
            st.json(data["last_cycle"])
        st.subheader("Live Process Log")
        st.code(readable, language="text")

    st.divider()
    auto = st.checkbox("Auto-refresh every 3 seconds", value=False)
    if auto:
        time.sleep(3)
        st.rerun()


if __name__ == "__main__":
    main()
