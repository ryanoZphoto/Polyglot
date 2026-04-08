"""
Market-Making Bot -- Streamlit UI

Run with:  streamlit run mm_ui.py
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

# ── page config ──

st.set_page_config(
    page_title="Market Maker Bot",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── constants ──

_RUNS_DIR = Path("mm_runs")
_ENV_PATH = Path(".env")
_PID_KEY = "mm_bot_pid"


def _find_latest_run() -> Path | None:
    if not _RUNS_DIR.exists():
        return None
    runs = sorted(_RUNS_DIR.iterdir(), reverse=True)
    for r in runs:
        if r.is_dir() and (r / "state.sqlite3").exists():
            return r
    return runs[0] if runs else None


def _get_run_paths() -> tuple[str, str, str]:
    run = _find_latest_run()
    if run is not None:
        return (
            str(run / "state.sqlite3"),
            str(run / "runtime.log"),
            str(run / "cycle_report.jsonl"),
        )
    return (
        os.getenv("MM_STATE_DB_PATH", "mm_bot_state.sqlite3"),
        os.getenv("MM_LOG_PATH", "mm_runtime.log"),
        os.getenv("MM_REPORT_PATH", "mm_cycle_report.jsonl"),
    )


_DB_PATH, _LOG_PATH, _REPORT_PATH = _get_run_paths()


# ── helpers ──

def _get_db() -> sqlite3.Connection | None:
    if not Path(_DB_PATH).exists():
        return None
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _query(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = _query(sql, params)
    return rows[0] if rows else None


def _read_env() -> dict[str, str]:
    env = {}
    if not _ENV_PATH.exists():
        return env
    for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v and len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        env[k] = v
    return env


def _write_env(updates: dict[str, str]) -> None:
    existing = []
    if _ENV_PATH.exists():
        existing = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    result = []
    updated_keys = set()
    for raw in existing:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            clean = stripped
            if clean.lower().startswith("export "):
                clean = clean[7:].strip()
            key = clean.split("=", 1)[0].strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        result.append(raw)
    for key, val in updates.items():
        if key not in updated_keys:
            result.append(f"{key}={val}")
    _ENV_PATH.write_text("\n".join(result) + "\n", encoding="utf-8")


def _is_running() -> bool:
    pid = st.session_state.get(_PID_KEY)
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _start_bot() -> None:
    if _is_running():
        st.toast("Bot is already running", icon="⚠️")
        return
    proc = subprocess.Popen(
        [sys.executable, "-m", "market_maker.main"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    st.session_state[_PID_KEY] = proc.pid
    st.toast(f"Bot started (PID {proc.pid})", icon="✅")


def _stop_bot() -> None:
    pid = st.session_state.get(_PID_KEY)
    if pid is None:
        st.toast("No bot running", icon="⚠️")
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.TimeoutExpired):
        pass
    st.session_state[_PID_KEY] = None
    st.toast("Bot stopped", icon="🛑")


def _emergency_stop() -> None:
    _stop_bot()
    _write_env({"MM_EMERGENCY_STOP": "true"})
    st.toast("EMERGENCY STOP: bot killed and flag set", icon="🚨")


# ── sidebar ──

with st.sidebar:
    st.title("🏦 Market Maker")

    env = _read_env()
    current_mode = env.get("MM_MODE", "dry_run")

    running = _is_running()
    status_color = "🟢" if running else "🔴"
    st.markdown(f"**Status:** {status_color} {'Running' if running else 'Stopped'}")
    st.markdown(f"**Mode:** `{current_mode}`")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("▶ Start", width="stretch", disabled=running):
            _start_bot()
            st.rerun()
    with col2:
        if st.button("⏹ Stop", width="stretch", disabled=not running):
            _stop_bot()
            st.rerun()
    with col3:
        if st.button("🚨 E-Stop", width="stretch", type="primary"):
            _emergency_stop()
            st.rerun()

    st.divider()

    st.subheader("Quick Settings")

    new_mode = st.selectbox("Mode", ["dry_run", "live"], index=0 if current_mode == "dry_run" else 1)

    new_half_spread = st.number_input(
        "Half Spread", value=float(env.get("MM_HALF_SPREAD", "0.02")),
        min_value=0.001, max_value=0.20, step=0.005, format="%.3f",
    )
    new_quote_size = st.number_input(
        "Quote Size (shares)", value=float(env.get("MM_QUOTE_SIZE", "20")),
        min_value=1.0, max_value=1000.0, step=5.0,
    )
    new_max_inv = st.number_input(
        "Max Inventory / Market", value=float(env.get("MM_MAX_INVENTORY_PER_MARKET", "100")),
        min_value=1.0, max_value=5000.0, step=10.0,
    )
    new_skew = st.number_input(
        "Skew Factor", value=float(env.get("MM_SKEW_FACTOR", "0.5")),
        min_value=0.0, max_value=2.0, step=0.1, format="%.2f",
    )
    new_poll = st.number_input(
        "Poll Interval (sec)", value=float(env.get("MM_POLL_INTERVAL_SECONDS", "5")),
        min_value=1.0, max_value=120.0, step=1.0,
    )
    new_max_exposure = st.number_input(
        "Max Total Exposure ($)", value=float(env.get("MM_MAX_TOTAL_EXPOSURE_USD", "500")),
        min_value=10.0, max_value=50000.0, step=50.0,
    )
    new_max_loss = st.number_input(
        "Max Daily Loss ($)", value=float(env.get("MM_MAX_DAILY_LOSS_USD", "50")),
        min_value=1.0, max_value=10000.0, step=10.0,
    )

    if st.button("💾 Save & Restart", width="stretch"):
        _write_env({
            "MM_MODE": new_mode,
            "MM_HALF_SPREAD": str(new_half_spread),
            "MM_QUOTE_SIZE": str(new_quote_size),
            "MM_MAX_INVENTORY_PER_MARKET": str(new_max_inv),
            "MM_SKEW_FACTOR": str(new_skew),
            "MM_POLL_INTERVAL_SECONDS": str(new_poll),
            "MM_MAX_TOTAL_EXPOSURE_USD": str(new_max_exposure),
            "MM_MAX_DAILY_LOSS_USD": str(new_max_loss),
        })
        if running:
            _stop_bot()
            time.sleep(1)
            _start_bot()
        st.cache_data.clear()
        st.toast("Settings saved", icon="💾")
        st.rerun()

    st.divider()
    if st.button("🔄 Refresh Data", width="stretch"):
        st.cache_data.clear()
        st.rerun()


# ── tabs ──

tab_dashboard, tab_markets, tab_inventory, tab_perf, tab_risk, tab_logs = st.tabs(
    ["📊 Dashboard", "🏪 Markets", "📦 Inventory", "💰 Performance", "🛡️ Risk", "📋 Logs"]
)


# ═══════════════════════════════════════════════════════════════════
#  TAB 1 -- Dashboard
# ═══════════════════════════════════════════════════════════════════

with tab_dashboard:
    st.header("Dashboard")

    cycle_count_row = _query_one("SELECT COUNT(*) c FROM mm_cycles")
    cycle_count = cycle_count_row["c"] if cycle_count_row else 0

    fill_count_row = _query_one("SELECT COUNT(*) c FROM mm_fills")
    fill_count = fill_count_row["c"] if fill_count_row else 0

    today = datetime.now(timezone.utc).date().isoformat()
    daily_pnl_row = _query_one(
        "SELECT COALESCE(SUM(realized_pnl),0) p FROM mm_fills WHERE substr(created_at,1,10)=?",
        (today,),
    )
    daily_pnl = daily_pnl_row["p"] if daily_pnl_row else 0.0

    total_pnl_row = _query_one("SELECT COALESCE(SUM(realized_pnl),0) p FROM mm_fills")
    total_pnl = total_pnl_row["p"] if total_pnl_row else 0.0

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Cycles Run", f"{cycle_count:,}")
    col_b.metric("Total Fills", f"{fill_count:,}")
    col_c.metric("Today's PnL", f"${daily_pnl:,.4f}")
    col_d.metric("All-Time PnL", f"${total_pnl:,.4f}")

    st.divider()

    st.subheader("Active Quotes (Latest Cycle)")
    latest_cycle_row = _query_one("SELECT cycle_id FROM mm_cycles ORDER BY started_at DESC LIMIT 1")
    if latest_cycle_row:
        latest_quotes = _query(
            "SELECT * FROM mm_quotes WHERE cycle_id=? ORDER BY market_id",
            (latest_cycle_row["cycle_id"],),
        )
        if latest_quotes:
            for q in latest_quotes:
                with st.container(border=True):
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.markdown(f"**{q.get('outcome', '?')}**")
                    c2.metric("Fair Value", f"{q.get('fair_value', 0):.3f}")
                    c3.metric("Bid", f"{q.get('bid_price', 0):.3f} × {q.get('bid_size', 0):.0f}")
                    c4.metric("Ask", f"{q.get('ask_price', 0):.3f} × {q.get('ask_size', 0):.0f}")
                    c5.metric("Spread", f"{q.get('spread', 0):.4f}")
        else:
            st.info("No quotes in the latest cycle.")
    else:
        st.info("No cycles recorded yet. Start the bot to begin.")

    st.subheader("Recent Fills")
    recent_fills = _query("SELECT * FROM mm_fills ORDER BY created_at DESC LIMIT 15")
    if recent_fills:
        st.dataframe(recent_fills, width="stretch", hide_index=True)
    else:
        st.info("No fills yet.")


# ═══════════════════════════════════════════════════════════════════
#  TAB 2 -- Markets
# ═══════════════════════════════════════════════════════════════════

with tab_markets:
    st.header("Markets Being Quoted")

    env = _read_env()
    current_markets_str = env.get("MM_MARKETS", "auto")
    st.markdown(f"**Market selection:** `{current_markets_str}`")

    market_quotes = _query("""
        SELECT q.market_id, q.token_id, q.outcome,
               q.bid_price, q.bid_size, q.ask_price, q.ask_size,
               q.fair_value, q.spread, q.skew_applied, q.created_at
        FROM mm_quotes q
        INNER JOIN (
            SELECT token_id, MAX(created_at) max_ts
            FROM mm_quotes
            GROUP BY token_id
        ) latest ON q.token_id = latest.token_id AND q.created_at = latest.max_ts
        ORDER BY q.market_id, q.outcome
    """)

    if market_quotes:
        for q in market_quotes:
            with st.container(border=True):
                st.markdown(f"**{q.get('outcome', '?')}** — `{q.get('token_id', '')[:20]}...`")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Fair Value", f"{q.get('fair_value', 0):.4f}")
                c2.metric("Your Bid", f"{q.get('bid_price', 0):.3f} × {q.get('bid_size', 0):.0f}")
                c3.metric("Your Ask", f"{q.get('ask_price', 0):.3f} × {q.get('ask_size', 0):.0f}")
                skew_val = q.get("skew_applied", 0) or 0
                c4.metric("Skew", f"{skew_val:+.5f}")
    else:
        st.info("No markets being quoted yet. Start the bot to begin.")

    st.divider()
    st.subheader("Manage Markets")
    st.markdown("Set `MM_MARKETS` in the sidebar or `.env` file. Use `auto` for automatic selection or a comma-separated list of market slugs.")

    new_markets_input = st.text_input("Market slugs (comma-separated, or 'auto')", value=current_markets_str)
    if st.button("Update Markets"):
        _write_env({"MM_MARKETS": new_markets_input.strip()})
        st.toast("Updated MM_MARKETS", icon="✅")
        st.cache_data.clear()
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
#  TAB 3 -- Inventory
# ═══════════════════════════════════════════════════════════════════

with tab_inventory:
    st.header("Inventory Positions")

    inventory_rows = _query("SELECT * FROM mm_inventory ORDER BY market_id")
    if inventory_rows:
        max_inv = float(env.get("MM_MAX_INVENTORY_PER_MARKET", "100"))
        for inv in inventory_rows:
            pos = float(inv.get("position_shares", 0))
            avg = float(inv.get("avg_entry_price", 0))
            outcome = inv.get("outcome", "?")
            if abs(pos) < 0.001:
                side_label = "FLAT"
                side_color = "gray"
            elif pos > 0:
                side_label = "LONG"
                side_color = "green"
            else:
                side_label = "SHORT"
                side_color = "red"

            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.markdown(f"**{outcome}**")
                c2.metric("Position", f"{pos:+.1f} shares")
                c3.metric("Avg Entry", f"${avg:.4f}")
                utilization = abs(pos) / max_inv * 100 if max_inv > 0 else 0
                c4.metric("Utilization", f"{utilization:.0f}%")
                c5.metric("Side", side_label)
                st.progress(min(1.0, abs(pos) / max(1.0, max_inv)))

        st.divider()
        exposure_row = _query_one(
            "SELECT COALESCE(SUM(ABS(position_shares) * avg_entry_price), 0) e FROM mm_inventory"
        )
        exposure = exposure_row["e"] if exposure_row else 0.0
        max_exposure = float(env.get("MM_MAX_TOTAL_EXPOSURE_USD", "500"))
        st.metric("Total Exposure", f"${exposure:,.2f} / ${max_exposure:,.2f}")
        st.progress(min(1.0, exposure / max(1.0, max_exposure)))
    else:
        st.info("No inventory positions. Fills will populate this tab.")


# ═══════════════════════════════════════════════════════════════════
#  TAB 4 -- Performance
# ═══════════════════════════════════════════════════════════════════

with tab_perf:
    st.header("Performance")

    all_fills = _query("SELECT created_at, realized_pnl, side, price, size FROM mm_fills ORDER BY created_at ASC")
    if all_fills:
        import pandas as pd

        df = pd.DataFrame(all_fills)
        df["cum_pnl"] = df["realized_pnl"].cumsum()

        st.subheader("Cumulative PnL")
        st.line_chart(df.set_index("created_at")["cum_pnl"])

        st.divider()

        st.subheader("Fill Summary")
        total_fills_count = len(df)
        total_realized = df["realized_pnl"].sum()
        avg_pnl = df["realized_pnl"].mean()
        win_count = (df["realized_pnl"] > 0).sum()
        loss_count = (df["realized_pnl"] < 0).sum()
        flat_count = (df["realized_pnl"] == 0).sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Fills", f"{total_fills_count:,}")
        c2.metric("Net Realized PnL", f"${total_realized:,.4f}")
        c3.metric("Avg PnL / Fill", f"${avg_pnl:,.4f}")
        win_rate = win_count / total_fills_count * 100 if total_fills_count > 0 else 0
        c4.metric("Win Rate", f"{win_rate:.1f}%")

        c5, c6, c7 = st.columns(3)
        c5.metric("Winning Fills", f"{win_count}")
        c6.metric("Losing Fills", f"{loss_count}")
        c7.metric("Flat Fills", f"{flat_count}")

        st.divider()
        st.subheader("Spread Earned per Fill")
        if not df.empty:
            st.bar_chart(df.set_index("created_at")["realized_pnl"])

        st.divider()
        st.subheader("Cycle PnL")
        cycle_data = _query("SELECT started_at, total_pnl, markets_quoted, orders_posted, fills_received FROM mm_cycles ORDER BY started_at DESC LIMIT 100")
        if cycle_data:
            st.dataframe(cycle_data, width="stretch", hide_index=True)
    else:
        st.info("No fills recorded yet. Performance data will appear after the bot processes fills.")


# ═══════════════════════════════════════════════════════════════════
#  TAB 5 -- Risk
# ═══════════════════════════════════════════════════════════════════

with tab_risk:
    st.header("Risk Monitor")

    env = _read_env()

    max_exposure_limit = float(env.get("MM_MAX_TOTAL_EXPOSURE_USD", "500"))
    max_daily_loss = float(env.get("MM_MAX_DAILY_LOSS_USD", "50"))
    max_inv = float(env.get("MM_MAX_INVENTORY_PER_MARKET", "100"))
    max_open = int(env.get("MM_MAX_OPEN_ORDERS", "40"))
    max_rate = int(env.get("MM_MAX_ORDERS_PER_MINUTE", "30"))
    e_stop = env.get("MM_EMERGENCY_STOP", "false").lower() in {"1", "true", "yes", "on"}

    exposure_row = _query_one("SELECT COALESCE(SUM(ABS(position_shares)*avg_entry_price),0) e FROM mm_inventory")
    current_exposure = exposure_row["e"] if exposure_row else 0.0

    today = datetime.now(timezone.utc).date().isoformat()
    daily_pnl_row = _query_one(
        "SELECT COALESCE(SUM(realized_pnl),0) p FROM mm_fills WHERE substr(created_at,1,10)=?", (today,),
    )
    current_daily_pnl = daily_pnl_row["p"] if daily_pnl_row else 0.0

    open_order_row = _query_one("SELECT COUNT(*) c FROM mm_orders WHERE status IN ('submitted','simulated')")
    current_open = open_order_row["c"] if open_order_row else 0

    if e_stop:
        st.error("🚨 EMERGENCY STOP IS ACTIVE -- bot will not trade")
    if st.button("Clear Emergency Stop"):
        _write_env({"MM_EMERGENCY_STOP": "false"})
        st.toast("Emergency stop cleared", icon="✅")
        st.rerun()

    st.divider()
    st.subheader("Risk Limits vs Current Values")

    risk_items = [
        ("Total Exposure ($)", current_exposure, max_exposure_limit),
        ("Daily Loss ($)", abs(current_daily_pnl), max_daily_loss),
        ("Open Orders", current_open, max_open),
    ]

    for label, current, limit in risk_items:
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{label}**")
            ratio = current / limit if limit > 0 else 0
            if ratio >= 0.9:
                c2.markdown(f":red[{current:,.2f} / {limit:,.2f}]")
            elif ratio >= 0.7:
                c2.markdown(f":orange[{current:,.2f} / {limit:,.2f}]")
            else:
                c2.markdown(f":green[{current:,.2f} / {limit:,.2f}]")
            c3.progress(min(1.0, ratio))

    st.divider()
    st.subheader("Risk Event Log")
    report_path = Path(_REPORT_PATH)
    if report_path.exists():
        blocked_events = []
        try:
            lines = report_path.read_text(encoding="utf-8").strip().splitlines()
            for line in reversed(lines[-100:]):
                try:
                    entry = json.loads(line)
                    if entry.get("diagnostics", {}).get("blocked"):
                        blocked_events.append({
                            "cycle": entry.get("cycle_id", "?"),
                            "reason": entry["diagnostics"]["blocked"],
                        })
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        if blocked_events:
            st.dataframe(blocked_events[:20], width="stretch", hide_index=True)
        else:
            st.success("No risk rejections recorded.")
    else:
        st.info("No cycle reports yet.")


# ═══════════════════════════════════════════════════════════════════
#  TAB 6 -- Logs
# ═══════════════════════════════════════════════════════════════════

with tab_logs:
    st.header("Logs")

    log_path = Path(_LOG_PATH)
    col_filter, col_lines = st.columns(2)
    with col_filter:
        level_filter = st.selectbox("Level Filter", ["ALL", "ERROR", "WARNING", "INFO"])
    with col_lines:
        num_lines = st.number_input("Lines to show", value=100, min_value=10, max_value=2000, step=50)

    if log_path.exists():
        try:
            raw_lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            tail = raw_lines[-int(num_lines):]

            if level_filter != "ALL":
                filtered = []
                for line in tail:
                    try:
                        entry = json.loads(line)
                        if entry.get("level", "").upper() == level_filter:
                            filtered.append(line)
                    except json.JSONDecodeError:
                        if level_filter.upper() in line.upper():
                            filtered.append(line)
                tail = filtered

            st.code("\n".join(tail), language="json")
        except Exception as e:
            st.error(f"Error reading log: {e}")
    else:
        st.info("Log file not found. Start the bot to generate logs.")

    st.divider()
    st.subheader("Latest Cycle Report")
    report_path = Path(_REPORT_PATH)
    if report_path.exists():
        try:
            lines = report_path.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                last = json.loads(lines[-1])
                st.json(last)
        except Exception:
            st.info("Could not parse last cycle report.")
    else:
        st.info("No cycle report file yet.")

    st.divider()
    st.subheader("Quote & Fill Audit Trail")
    recent_q = _query("SELECT * FROM mm_quotes ORDER BY created_at DESC LIMIT 30")
    if recent_q:
        st.markdown("**Recent Quotes**")
        st.dataframe(recent_q, width="stretch", hide_index=True)

    recent_f = _query("SELECT * FROM mm_fills ORDER BY created_at DESC LIMIT 30")
    if recent_f:
        st.markdown("**Recent Fills**")
        st.dataframe(recent_f, width="stretch", hide_index=True)

    if not recent_q and not recent_f:
        st.info("No quotes or fills recorded yet.")


# ── auto-refresh ──

st.markdown("---")
auto_refresh = st.checkbox("Auto-refresh every 5 seconds", value=True)
if auto_refresh:
    time.sleep(5)
    st.rerun()
