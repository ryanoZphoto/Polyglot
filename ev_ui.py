"""
Event-Driven Trader Bot -- Streamlit UI

Run with:  streamlit run ev_ui.py
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

st.set_page_config(
    page_title="Event Trader",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

_RUNS_DIR = Path("ev_runs")
_CONTROLS_PATH = Path("ev_controls.json")
_PID_KEY = "ev_bot_pid"

TIER_COLORS = {"longshot": "#e74c3c", "mid": "#f39c12", "highprob": "#2ecc71"}
TIER_LABELS = {"longshot": "LONGSHOT", "mid": "MID-RANGE", "highprob": "HIGH PROB"}


# ── helpers ──

def _find_latest_run() -> Path | None:
    if not _RUNS_DIR.exists():
        return None
    runs = sorted(_RUNS_DIR.iterdir(), reverse=True)
    for r in runs:
        if r.is_dir() and (r / "state.sqlite3").exists():
            return r
    return runs[0] if runs else None


def _all_runs() -> list[Path]:
    if not _RUNS_DIR.exists():
        return []
    return sorted([r for r in _RUNS_DIR.iterdir() if r.is_dir()], reverse=True)


def _get_db(db_path: str) -> sqlite3.Connection | None:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _query(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_db(db_path)
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _scalar(db_path: str, sql: str, params: tuple = (), default: float = 0.0) -> float:
    conn = _get_db(db_path)
    if conn is None:
        return default
    try:
        row = conn.execute(sql, params).fetchone()
        return float(row[0]) if row and row[0] is not None else default
    except Exception:
        return default
    finally:
        conn.close()


def _is_running() -> bool:
    pid = st.session_state.get(_PID_KEY)
    if pid is not None:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            pass
    return len(_find_bot_pids()) > 0


def _start_bot() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "event_trader.main"],
        cwd=str(Path.cwd()),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        if sys.platform == "win32" else 0,
    )
    st.session_state[_PID_KEY] = proc.pid


def _find_bot_pids() -> list[int]:
    """Find all running event_trader.main processes."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
        )
        pids = []
        for line in result.stdout.splitlines():
            if "event_trader.main" in line:
                parts = [p.strip() for p in line.split(",") if p.strip()]
                for p in parts:
                    if p.isdigit():
                        pids.append(int(p))
        return pids
    except Exception:
        return []


def _stop_bot() -> None:
    pids_to_kill = set()

    tracked = st.session_state.get(_PID_KEY)
    if tracked:
        pids_to_kill.add(tracked)

    pids_to_kill.update(_find_bot_pids())

    for pid in pids_to_kill:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    st.session_state.pop(_PID_KEY, None)


def _read_controls() -> dict:
    defaults = {"paused": False, "max_invested_usd": 100.0}
    if not _CONTROLS_PATH.exists():
        return defaults
    try:
        data = json.loads(_CONTROLS_PATH.read_text(encoding="utf-8"))
        defaults.update(data)
    except Exception:
        pass
    return defaults


def _write_controls(controls: dict) -> None:
    _CONTROLS_PATH.write_text(json.dumps(controls, indent=2) + "\n", encoding="utf-8")


def _tier_badge(tier: str) -> str:
    color = TIER_COLORS.get(tier, "#95a5a6")
    label = TIER_LABELS.get(tier, tier.upper())
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.75em;font-weight:600;">{label}</span>'
    )


def _pnl_color(val: float) -> str:
    if val > 0:
        return "#2ecc71"
    if val < 0:
        return "#e74c3c"
    return "#95a5a6"


def _guess_tier(entry_price: float) -> str:
    if entry_price <= 0.30:
        return "longshot"
    if entry_price >= 0.55:
        return "highprob"
    return "mid"


# ══════════════════════════════════════════════════════════════════════
# SIDEBAR -- controls that are always visible
# ══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("Event Trader")

    running = _is_running()
    if running:
        st.success("Bot is RUNNING")
    else:
        st.warning("Bot is STOPPED")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Bot", disabled=running, use_container_width=True, type="primary"):
            _start_bot()
            time.sleep(1)
            st.rerun()
    with col2:
        if st.button("Stop Bot", disabled=not running, use_container_width=True):
            _stop_bot()
            st.rerun()

    st.divider()

    # ── LIVE CONTROLS ──
    st.subheader("Live Controls")
    controls = _read_controls()

    paused = st.toggle(
        "Pause New Bets",
        value=controls.get("paused", False),
        help="Stops all new entries. Existing positions keep their normal target/stop rules.",
    )

    max_invested = st.number_input(
        "Max Total Invested ($)",
        min_value=5.0,
        max_value=10000.0,
        value=float(controls.get("max_invested_usd", 100.0)),
        step=5.0,
        help="Bot will never invest more than this total across all positions.",
    )

    new_controls = {"paused": paused, "max_invested_usd": max_invested}
    if new_controls != controls:
        _write_controls(new_controls)
        st.toast("Controls updated -- bot will pick up changes next cycle")

    if paused:
        st.info("New bets paused. Existing positions still monitored with normal exit rules.")

    st.divider()

    # ── run selector ──
    runs = _all_runs()
    run_names = [r.name for r in runs] if runs else ["(no runs)"]
    selected_run_name = st.selectbox("View Run", run_names, index=0)
    selected_run = None
    for r in runs:
        if r.name == selected_run_name:
            selected_run = r
            break

    if selected_run and (selected_run / "state.sqlite3").exists():
        db_path = str(selected_run / "state.sqlite3")
        log_path = str(selected_run / "runtime.log")
        report_path = str(selected_run / "cycle_report.jsonl")
        config_path = selected_run / "config.json"
    else:
        db_path = "ev_bot_state.sqlite3"
        log_path = "ev_runtime.log"
        report_path = "ev_cycle_report.jsonl"
        config_path = None

    st.divider()
    auto = st.toggle("Auto-refresh (5s)", value=True)

    if config_path and config_path.exists():
        with st.expander("Run Config"):
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                for k in ("private_key", "api_key", "api_secret", "api_passphrase"):
                    cfg.pop(k, None)
                st.json(cfg)
            except Exception:
                st.text("Could not load config")


# ══════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════

tab_stats, tab_live, tab_positions, tab_signals, tab_perf, tab_risk, tab_logs = st.tabs(
    ["All-Time Stats", "Live Dashboard", "Positions", "Signals", "Performance", "Risk", "Logs"]
)


# ── ALL-TIME STATS ──

with tab_stats:
    all_run_dbs = []
    for r in _all_runs():
        db = r / "state.sqlite3"
        if db.exists():
            all_run_dbs.append(str(db))

    agg_total_buys = 0
    agg_total_sells = 0
    agg_total_wins = 0
    agg_total_losses = 0
    agg_realized_pnl = 0.0
    agg_unrealized_pnl = 0.0
    agg_total_invested = 0.0
    agg_total_cycles = 0
    agg_best_trade_pnl = 0.0
    agg_best_trade_desc = "N/A"
    agg_worst_trade_pnl = 0.0
    agg_worst_trade_desc = "N/A"
    agg_biggest_position = 0.0
    agg_biggest_position_desc = "N/A"
    agg_open_positions = 0
    agg_closed_positions = 0
    all_closed_trades: list[dict] = []
    all_open_trades: list[dict] = []

    for rdb in all_run_dbs:
        try:
            c = sqlite3.connect(rdb, check_same_thread=False)
            c.row_factory = sqlite3.Row

            agg_total_buys += c.execute("SELECT COUNT(*) FROM ev_fills WHERE side='BUY'").fetchone()[0]
            agg_total_sells += c.execute("SELECT COUNT(*) FROM ev_fills WHERE side='SELL'").fetchone()[0]
            agg_total_cycles += c.execute("SELECT COUNT(*) FROM ev_cycles").fetchone()[0]

            open_rows = [dict(r) for r in c.execute(
                "SELECT outcome, question, entry_price, current_price, contracts, "
                "cost_basis_usd, entry_at FROM ev_positions WHERE status='open'"
            ).fetchall()]
            agg_open_positions += len(open_rows)
            for op in open_rows:
                val = op["current_price"] * op["contracts"]
                cost = op["cost_basis_usd"]
                op["pnl"] = val - cost
                op["value"] = val
                agg_unrealized_pnl += op["pnl"]
                agg_total_invested += cost
                all_open_trades.append(op)

            closed_rows = [dict(r) for r in c.execute(
                "SELECT outcome, question, entry_price, contracts, cost_basis_usd, "
                "realized_pnl, close_reason, closed_at FROM ev_positions WHERE status='closed'"
            ).fetchall()]
            agg_closed_positions += len(closed_rows)
            for cr in closed_rows:
                rpnl = cr["realized_pnl"]
                agg_total_invested += cr["cost_basis_usd"]
                agg_realized_pnl += rpnl
                if rpnl > 0:
                    agg_total_wins += 1
                else:
                    agg_total_losses += 1
                all_closed_trades.append(cr)
                if rpnl > agg_best_trade_pnl:
                    agg_best_trade_pnl = rpnl
                    agg_best_trade_desc = f"{cr['outcome']} -- {cr['question'][:50]}"
                if rpnl < agg_worst_trade_pnl:
                    agg_worst_trade_pnl = rpnl
                    agg_worst_trade_desc = f"{cr['outcome']} -- {cr['question'][:50]}"

            c.close()
        except Exception:
            pass

    agg_total_pnl = agg_realized_pnl + agg_unrealized_pnl
    agg_closed_total = agg_total_wins + agg_total_losses
    agg_win_rate = (agg_total_wins / agg_closed_total * 100) if agg_closed_total > 0 else 0
    agg_roi = (agg_total_pnl / agg_total_invested * 100) if agg_total_invested > 0 else 0
    agg_avg_trade = (agg_realized_pnl / agg_closed_total) if agg_closed_total > 0 else 0

    st.subheader("Lifetime Performance")

    m1, m2, m3, m4 = st.columns(4)
    pnl_color = "green" if agg_total_pnl >= 0 else "red"
    m1.markdown(
        f'**Total P&L**<br><span style="font-size:2em;color:{pnl_color}">'
        f'${agg_total_pnl:+.4f}</span>',
        unsafe_allow_html=True,
    )
    m2.metric("Total ROI", f"{agg_roi:+.2f}%")
    m3.metric("Win Rate", f"{agg_win_rate:.0f}%")
    m4.metric("Runs Analyzed", len(all_run_dbs))

    st.divider()

    st.subheader("Trade Summary")
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Total Buys", agg_total_buys)
    s2.metric("Total Sells", agg_total_sells)
    s3.metric("Wins", agg_total_wins)
    s4.metric("Losses", agg_total_losses)
    s5.metric("Open Positions", agg_open_positions)

    s6, s7, s8, s9, s10 = st.columns(5)
    s6.metric("Realized P&L", f"${agg_realized_pnl:+.4f}")
    s7.metric("Unrealized P&L", f"${agg_unrealized_pnl:+.4f}")
    s8.metric("Avg Trade P&L", f"${agg_avg_trade:+.4f}")
    s9.metric("Total Invested", f"${agg_total_invested:.2f}")
    s10.metric("Total Cycles", f"{agg_total_cycles:,}")

    st.divider()

    st.subheader("Best & Worst")
    bw1, bw2 = st.columns(2)
    with bw1:
        st.markdown("**Best Trade**")
        if agg_best_trade_pnl > 0:
            st.markdown(
                f'<span style="color:green;font-size:1.5em">${agg_best_trade_pnl:+.4f}</span><br>'
                f'{agg_best_trade_desc}',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No winning trades yet")
    with bw2:
        st.markdown("**Worst Trade**")
        if agg_worst_trade_pnl < 0:
            st.markdown(
                f'<span style="color:red;font-size:1.5em">${agg_worst_trade_pnl:+.4f}</span><br>'
                f'{agg_worst_trade_desc}',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No losing trades yet")

    st.divider()

    if all_closed_trades:
        st.subheader("Closed Trade History")
        for ct in sorted(all_closed_trades, key=lambda x: x.get("closed_at", ""), reverse=True):
            pnl = ct["realized_pnl"]
            tier = _guess_tier(ct["entry_price"])
            pnl_c = _pnl_color(pnl)
            st.markdown(
                f'{_tier_badge(tier)} &nbsp; **{ct["outcome"]}** -- {ct["question"][:70]} &nbsp; '
                f'<span style="color:{pnl_c};font-weight:600">${pnl:+.4f}</span> &nbsp; '
                f'{ct.get("close_reason", "?")} &nbsp; Entry: ${ct["entry_price"]:.3f}',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No closed trades across any run yet.")

    if all_open_trades:
        st.subheader(f"All Open Positions ({len(all_open_trades)})")
        open_sorted = sorted(all_open_trades, key=lambda x: x.get("pnl", 0), reverse=True)
        for ot in open_sorted[:20]:
            pnl = ot["pnl"]
            tier = _guess_tier(ot["entry_price"])
            pnl_c = _pnl_color(pnl)
            st.markdown(
                f'{_tier_badge(tier)} &nbsp; **{ot["outcome"]}** -- {ot["question"][:70]} &nbsp; '
                f'<span style="color:{pnl_c}">${pnl:+.4f}</span> &nbsp; '
                f'Entry: ${ot["entry_price"]:.3f} &nbsp; Now: ${ot["current_price"]:.3f}',
                unsafe_allow_html=True,
            )
        if len(all_open_trades) > 20:
            st.caption(f"...and {len(all_open_trades) - 20} more")


# ── LIVE DASHBOARD ──

with tab_live:
    # top-level metrics
    open_pos = int(_scalar(db_path, "SELECT COUNT(*) FROM ev_positions WHERE status='open'"))
    total_cycles = int(_scalar(db_path, "SELECT COUNT(*) FROM ev_cycles"))
    unrealized = _scalar(
        db_path,
        "SELECT COALESCE(SUM((current_price - entry_price) * contracts), 0) "
        "FROM ev_positions WHERE status='open'",
    )
    today = datetime.now(timezone.utc).date().isoformat()
    realized = _scalar(
        db_path,
        "SELECT COALESCE(SUM(realized_pnl), 0) FROM ev_fills WHERE substr(created_at,1,10)=?",
        (today,),
    )
    total_cost = _scalar(
        db_path,
        "SELECT COALESCE(SUM(cost_basis_usd), 0) FROM ev_positions WHERE status='open'",
    )
    total_value = _scalar(
        db_path,
        "SELECT COALESCE(SUM(current_price * contracts), 0) FROM ev_positions WHERE status='open'",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open Positions", open_pos)
    c2.metric("Cycles Run", total_cycles)
    c3.metric("Unrealized P&L", f"${unrealized:+.4f}")
    c4.metric("Realized Today", f"${realized:+.4f}")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Total Invested", f"${total_cost:.2f}")
    c6.metric("Current Value", f"${total_value:.2f}")
    roi = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
    c7.metric("ROI", f"{roi:+.1f}%")
    budget_used = total_cost / max_invested * 100 if max_invested > 0 else 0
    c8.metric("Budget Used", f"${total_cost:.0f} / ${max_invested:.0f}")

    # ── tier breakdown ──
    st.subheader("Positions by Tier")
    positions_all = _query(db_path, "SELECT * FROM ev_positions WHERE status='open'")
    tier_counts = {"longshot": 0, "mid": 0, "highprob": 0}
    tier_cost = {"longshot": 0.0, "mid": 0.0, "highprob": 0.0}
    tier_value = {"longshot": 0.0, "mid": 0.0, "highprob": 0.0}
    for p in positions_all:
        t = _guess_tier(p["entry_price"])
        tier_counts[t] += 1
        tier_cost[t] += p["cost_basis_usd"]
        tier_value[t] += p["current_price"] * p["contracts"]

    tc1, tc2, tc3 = st.columns(3)
    for col, tier in zip([tc1, tc2, tc3], ["longshot", "mid", "highprob"]):
        pnl = tier_value[tier] - tier_cost[tier]
        with col:
            st.markdown(f"### {_tier_badge(tier)}", unsafe_allow_html=True)
            st.metric("Positions", tier_counts[tier])
            st.metric("Invested", f"${tier_cost[tier]:.2f}")
            if tier_counts[tier] > 0:
                st.metric("Value", f"${tier_value[tier]:.2f}")
                st.metric("P&L", f"${pnl:+.4f}", delta=f"{pnl:+.4f}")

    # ── live activity feed ──
    st.subheader("Live Activity")
    if Path(log_path).exists():
        try:
            lines = Path(log_path).read_text(encoding="utf-8").splitlines()
            activity = []
            for line in lines[-200:]:
                try:
                    entry = json.loads(line)
                    msg = entry.get("msg", "")
                    ts = entry.get("ts", "?")[11:19]
                    if "DRY_BUY" in msg or "LIVE_BUY" in msg:
                        activity.append(f"**{ts}** -- BUY {msg.split('BUY')[1].strip()[:100]}")
                    elif "DRY_SELL" in msg or "LIVE_SELL" in msg:
                        activity.append(f"**{ts}** -- SELL {msg.split('SELL:')[1].strip()[:100]}")
                    elif "EXIT_SIGNAL" in msg:
                        activity.append(f"**{ts}** -- EXIT {msg.split('EXIT_SIGNAL:')[1].strip()[:100]}")
                    elif "BUDGET_CAP" in msg:
                        activity.append(f"**{ts}** -- BUDGET LIMIT HIT")
                    elif "PAUSED" in msg:
                        activity.append(f"**{ts}** -- PAUSED by user")
                    elif "cycle" in msg and "done" in msg:
                        parts = msg.split("done in")[1].strip() if "done in" in msg else ""
                        activity.append(f"**{ts}** -- Cycle complete: {parts[:80]}")
                except (json.JSONDecodeError, IndexError):
                    pass

            if activity:
                for item in reversed(activity[-15:]):
                    st.markdown(item)
            else:
                st.caption("Waiting for bot activity...")
        except Exception:
            st.caption("Waiting for log file...")
    else:
        st.caption("No log file yet. Start the bot.")

    # ── recent cycles table ──
    st.subheader("Recent Cycles")
    cycles = _query(db_path, "SELECT * FROM ev_cycles ORDER BY started_at DESC LIMIT 10")
    if cycles:
        display_cols = [
            "cycle_number", "started_at", "markets_scanned", "signals_found",
            "entries_placed", "exits_placed", "positions_open",
            "unrealized_pnl", "realized_pnl",
        ]
        filtered = [{k: c.get(k) for k in display_cols} for c in cycles]
        st.dataframe(filtered, width="stretch", hide_index=True)
    else:
        st.info("No cycles yet. Hit **Start Bot** to begin.")


# ── POSITIONS ──

with tab_positions:
    st.subheader("Open Positions")
    positions = _query(
        db_path,
        "SELECT * FROM ev_positions WHERE status='open' ORDER BY entry_at DESC",
    )
    if positions:
        for p in positions:
            entry = p["entry_price"]
            current = p["current_price"]
            contracts = p["contracts"]
            cost = p["cost_basis_usd"]
            value = current * contracts
            pnl = value - cost
            pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
            target = p["target_price"]
            stop = p["stop_loss_price"]
            high_water = p["high_water_price"]
            tier = _guess_tier(entry)

            progress_to_target = max(0, min(1, (current - entry) / (target - entry))) if target > entry else 0
            progress_to_stop = max(0, min(1, (entry - current) / (entry - stop))) if entry > stop else 0

            pnl_c = _pnl_color(pnl)

            st.markdown(
                f'{_tier_badge(tier)} &nbsp; **{p["outcome"]}** -- {p["question"][:90]}',
                unsafe_allow_html=True,
            )

            mc1, mc2, mc3, mc4, mc5 = st.columns(5)
            mc1.metric("Entry", f"${entry:.3f}")
            mc2.metric("Now", f"${current:.3f}")
            mc3.metric("Contracts", f"{contracts:.0f}")
            mc4.metric("Cost", f"${cost:.2f}")
            mc5.markdown(
                f'**P&L**<br><span style="font-size:1.4em;color:{pnl_c}">'
                f'${pnl:+.4f} ({pnl_pct:+.1f}%)</span>',
                unsafe_allow_html=True,
            )

            bc1, bc2 = st.columns(2)
            with bc1:
                st.caption(f"Target: ${target:.3f}")
                st.progress(progress_to_target, text=f"{progress_to_target*100:.0f}% to target")
            with bc2:
                st.caption(f"Stop: ${stop:.3f}")
                if progress_to_stop > 0:
                    st.progress(progress_to_stop, text=f"{progress_to_stop*100:.0f}% to stop")
                else:
                    st.progress(0.0, text="Safe")

            st.caption(f"High water: ${high_water:.3f} | Entered: {p['entry_at'][:19]}")
            st.divider()
    else:
        st.info("No open positions. The bot will buy contracts when it finds good opportunities.")

    st.subheader("Closed Positions")
    closed = _query(
        db_path,
        "SELECT * FROM ev_positions WHERE status='closed' ORDER BY closed_at DESC LIMIT 30",
    )
    if closed:
        for c in closed:
            pnl = c["realized_pnl"]
            tier = _guess_tier(c["entry_price"])
            st.markdown(
                f'{_tier_badge(tier)} &nbsp; **{c["outcome"]}** -- {c["question"][:80]} &nbsp; '
                f'<span style="color:{_pnl_color(pnl)}">P&L: ${pnl:+.4f}</span> &nbsp; '
                f'Reason: {c.get("close_reason", "?")} &nbsp; Entry: ${c["entry_price"]:.3f}',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No closed positions yet.")


# ── SIGNALS ──

with tab_signals:
    st.subheader("Recent Signals")
    sigs = _query(db_path, "SELECT * FROM ev_signals ORDER BY created_at DESC LIMIT 60")
    if sigs:
        acted = [s for s in sigs if s.get("acted_on")]
        not_acted = [s for s in sigs if not s.get("acted_on")]

        if acted:
            st.markdown(f"**Acted on ({len(acted)})** -- bot bought these:")
            for s in acted[:20]:
                tier = _guess_tier(s["entry_price"])
                st.markdown(
                    f'{_tier_badge(tier)} &nbsp; **{s["outcome"]}** -- {s["question"][:80]} &nbsp; '
                    f'Entry: ${s["entry_price"]:.3f} | '
                    f'Target: ${s["target_price"]:.3f} | '
                    f'Conf: {s["confidence"]:.2f} | '
                    f'{s.get("reason", "")[:60]}',
                    unsafe_allow_html=True,
                )
            st.divider()

        if not_acted:
            with st.expander(f"Observed only ({len(not_acted)}) -- saw but didn't act"):
                display_cols = [
                    "outcome", "question", "entry_price", "target_price",
                    "stop_loss_price", "confidence", "reason", "created_at",
                ]
                filtered = [{k: s.get(k) for k in display_cols} for s in not_acted[:30]]
                st.dataframe(filtered, width="stretch", hide_index=True)
    else:
        st.info("No signals detected yet.")


# ── PERFORMANCE ──

with tab_perf:
    st.subheader("Trade Performance")

    total_trades = int(_scalar(db_path, "SELECT COUNT(*) FROM ev_fills WHERE side='SELL'"))
    wins = int(_scalar(db_path, "SELECT COUNT(*) FROM ev_fills WHERE side='SELL' AND realized_pnl > 0"))
    losses = int(_scalar(db_path, "SELECT COUNT(*) FROM ev_fills WHERE side='SELL' AND realized_pnl <= 0"))
    total_pnl = _scalar(db_path, "SELECT COALESCE(SUM(realized_pnl), 0) FROM ev_fills WHERE side='SELL'")
    avg_win = _scalar(
        db_path,
        "SELECT COALESCE(AVG(realized_pnl), 0) FROM ev_fills WHERE side='SELL' AND realized_pnl > 0",
    )
    avg_loss = _scalar(
        db_path,
        "SELECT COALESCE(AVG(realized_pnl), 0) FROM ev_fills WHERE side='SELL' AND realized_pnl <= 0",
    )

    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("Total Closed Trades", total_trades)
    sc2.metric("Wins / Losses", f"{wins} / {losses}")
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    sc3.metric("Win Rate", f"{win_rate:.0f}%")

    sc4, sc5, sc6 = st.columns(3)
    sc4.metric("Total Realized P&L", f"${total_pnl:+.4f}")
    sc5.metric("Avg Win", f"${avg_win:+.4f}")
    sc6.metric("Avg Loss", f"${avg_loss:+.4f}")

    fills = _query(db_path, "SELECT * FROM ev_fills ORDER BY created_at DESC LIMIT 100")
    if fills:
        pnl_data = _query(
            db_path,
            "SELECT created_at, realized_pnl FROM ev_fills WHERE side='SELL' ORDER BY created_at ASC",
        )
        if pnl_data:
            cumulative = 0.0
            chart_data = []
            for row in pnl_data:
                cumulative += row["realized_pnl"]
                chart_data.append({"time": row["created_at"][:19], "cumulative_pnl": cumulative})
            st.subheader("Cumulative P&L Over Time")
            st.line_chart(chart_data, x="time", y="cumulative_pnl")

        st.subheader("All Fills")
        st.dataframe(fills, width="stretch", hide_index=True)
    else:
        st.info("No trades completed yet. Positions need to hit target or stop to close.")


# ── RISK ──

with tab_risk:
    st.subheader("Position Risk Monitor")
    st.caption("Per-position distance to target and stop loss -- how close each trade is to triggering an exit.")

    risk_positions = _query(
        db_path,
        "SELECT position_id, outcome, question, entry_price, current_price, "
        "target_price, stop_loss_price, high_water_price, cost_basis_usd, "
        "contracts, entry_at "
        "FROM ev_positions WHERE status='open' ORDER BY entry_at DESC",
    )

    if risk_positions:
        for rp in risk_positions:
            entry = rp["entry_price"]
            current = rp["current_price"]
            target = rp["target_price"]
            stop = rp["stop_loss_price"]
            hw = rp["high_water_price"]
            contracts = rp["contracts"]
            cost = rp["cost_basis_usd"]
            value = current * contracts
            pnl = value - cost

            dist_to_target = ((target - current) / current * 100) if current > 0 else 0
            dist_to_stop = ((current - stop) / current * 100) if current > 0 else 0
            tier = _guess_tier(entry)

            pnl_c = _pnl_color(pnl)
            danger = dist_to_stop < 3
            safe = dist_to_target < 5

            st.markdown(
                f'{_tier_badge(tier)} &nbsp; **{rp["outcome"]}** -- {rp["question"][:80]}',
                unsafe_allow_html=True,
            )

            rc1, rc2, rc3, rc4, rc5 = st.columns(5)
            rc1.metric("Now", f"${current:.4f}")
            rc2.metric("To Target", f"{dist_to_target:+.1f}%", delta=f"${target:.3f}")
            rc3.metric("To Stop", f"{dist_to_stop:.1f}%", delta=f"${stop:.3f}")
            rc4.metric("High Water", f"${hw:.4f}")
            rc5.markdown(
                f'**P&L**<br><span style="color:{pnl_c};font-size:1.2em">${pnl:+.4f}</span>',
                unsafe_allow_html=True,
            )

            if danger:
                st.warning(f"Close to stop loss -- only {dist_to_stop:.1f}% away")
            if safe:
                st.success(f"Approaching target -- only {dist_to_target:.1f}% away")

            st.divider()
    else:
        st.info("No open positions to monitor.")

    st.subheader("Exposure by Market")
    market_exp = _query(
        db_path,
        "SELECT market_id, question, SUM(contracts * current_price) as exposure, "
        "SUM(cost_basis_usd) as invested "
        "FROM ev_positions WHERE status='open' GROUP BY market_id ORDER BY exposure DESC",
    )
    if market_exp:
        st.dataframe(market_exp, width="stretch", hide_index=True)

    st.subheader("Worst Case Scenario")
    st.caption("If every position hit its stop loss simultaneously:")
    total_stop_loss_value = sum(
        rp["stop_loss_price"] * rp["contracts"]
        for rp in risk_positions
    ) if risk_positions else 0
    total_cost = sum(rp["cost_basis_usd"] for rp in risk_positions) if risk_positions else 0
    max_loss = total_stop_loss_value - total_cost
    wc1, wc2, wc3 = st.columns(3)
    wc1.metric("Total Invested", f"${total_cost:.2f}")
    wc2.metric("Stop-Loss Value", f"${total_stop_loss_value:.2f}")
    wc3.metric("Max Drawdown", f"${max_loss:+.2f}")


# ── LOGS ──

with tab_logs:
    st.subheader("Live Log")
    if Path(log_path).exists():
        try:
            lines = Path(log_path).read_text(encoding="utf-8").splitlines()
            tail = lines[-80:] if len(lines) > 80 else lines
            display = []
            for line in tail:
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts", "?")[:19]
                    lvl = entry.get("level", "?")
                    msg = entry.get("msg", "")
                    if any(kw in msg for kw in ("SIGNAL", "DRY_BUY", "LIVE_BUY", "BUDGET_CAP", "PAUSED")):
                        display.append(f">>> {ts}  {lvl:5s}  {msg}")
                    elif any(kw in msg for kw in ("DRY_SELL", "LIVE_SELL", "EXIT_SIGNAL")):
                        display.append(f"!!! {ts}  {lvl:5s}  {msg}")
                    elif "PORTFOLIO" in msg or "POS:" in msg:
                        display.append(f"    {ts}  {lvl:5s}  {msg}")
                    else:
                        display.append(f"    {ts}  {lvl:5s}  {msg}")
                except json.JSONDecodeError:
                    display.append(line)
            st.code("\n".join(display), language="text")
        except Exception as e:
            st.error(f"Error reading log: {e}")
    else:
        st.info("No log file yet. Start the bot first.")

    st.subheader("Cycle Reports")
    if Path(report_path).exists():
        try:
            rlines = Path(report_path).read_text(encoding="utf-8").splitlines()
            tail = rlines[-5:] if len(rlines) > 5 else rlines
            for rline in reversed(tail):
                try:
                    entry = json.loads(rline)
                    st.json(entry)
                except json.JSONDecodeError:
                    st.text(rline)
        except Exception:
            pass


# ── auto refresh ──

if auto:
    time.sleep(5)
    st.rerun()
