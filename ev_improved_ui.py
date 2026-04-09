"""
Event-Driven Trader Bot (IMPROVED) -- Streamlit UI

Run with:  streamlit run ev_improved_ui.py
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
    page_title="Event Trader (Improved)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

_RUNS_DIR = Path("ev_runs_improved")
_CONTROLS_PATH = Path("ev_controls_improved.json")
_PID_KEY = "ev_improved_bot_pid"


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _all_runs() -> list[Path]:
    if not _RUNS_DIR.exists():
        return []
    return sorted([d for d in _RUNS_DIR.iterdir() if d.is_dir()], reverse=True)


def _read_controls() -> dict:
    if not _CONTROLS_PATH.exists():
        return {}
    try:
        return json.loads(_CONTROLS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_controls(data: dict):
    _CONTROLS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _is_bot_running() -> bool:
    controls = _read_controls()
    pid = controls.get(_PID_KEY)
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _start_bot():
    if _is_bot_running():
        st.warning("Bot already running!")
        return
    
    _RUNS_DIR.mkdir(exist_ok=True)
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "event_trader_improved.main"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    
    controls = _read_controls()
    controls[_PID_KEY] = proc.pid
    _write_controls(controls)
    
    st.success(f"Bot started (PID {proc.pid})")
    time.sleep(1)
    st.rerun()


def _stop_bot():
    controls = _read_controls()
    pid = controls.get(_PID_KEY)
    if not pid:
        st.info("No bot PID found.")
        return
    
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGTERM)
        st.success(f"Sent stop signal to PID {pid}")
        controls.pop(_PID_KEY, None)
        _write_controls(controls)
        time.sleep(1)
        st.rerun()
    except (OSError, ProcessLookupError):
        st.warning(f"PID {pid} not found (already stopped?)")
        controls.pop(_PID_KEY, None)
        _write_controls(controls)


def _query(db_path: str, sql: str, params=()) -> list[dict]:
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _scalar(db_path: str, sql: str, params=(), default=0.0):
    rows = _query(db_path, sql, params)
    if rows:
        val = list(rows[0].values())[0]
        return val if val is not None else default
    return default


def _tier_badge(tier: str) -> str:
    colors = {
        "longshot": "#FF6B6B",
        "mid": "#FFD93D",
        "highprob": "#6BCF7F",
    }
    return f'<span style="background:{colors.get(tier, "#999")};color:#000;padding:2px 6px;border-radius:3px;font-weight:600">{tier.upper()}</span>'


def _guess_tier(price: float) -> str:
    if price < 0.20:
        return "longshot"
    elif price < 0.60:
        return "mid"
    else:
        return "highprob"


def _pnl_color(pnl: float) -> str:
    return "#4CAF50" if pnl >= 0 else "#F44336"


# ══════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("⚡ Event Trader (Improved)")
    
    running = _is_bot_running()
    status_color = "🟢" if running else "🔴"
    st.markdown(f"### {status_color} {'Running' if running else 'Stopped'}")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Start", disabled=running, use_container_width=True):
            _start_bot()
    with col2:
        if st.button("⏹️ Stop", disabled=not running, use_container_width=True):
            _stop_bot()
    
    st.divider()
    
    max_invested = st.number_input("Max Total Invested ($)", value=100.0, step=10.0)
    
    st.divider()
    
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
        db_path = "ev_state_improved.db"
        log_path = "ev_trader_improved.log"
        report_path = "ev_report_improved.jsonl"
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
    
    for db in all_run_dbs:
        try:
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            cycles = c.execute("SELECT COUNT(*) FROM ev_cycles").fetchone()[0]
            agg_total_cycles += cycles
            
            buys = c.execute("SELECT COUNT(*) FROM ev_fills WHERE side='BUY'").fetchone()[0]
            sells = c.execute("SELECT COUNT(*) FROM ev_fills WHERE side='SELL'").fetchone()[0]
            agg_total_buys += buys
            agg_total_sells += sells
            
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
    
    st.header("📊 All-Time Statistics (Improved Version)")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Cycles", agg_total_cycles)
    c2.metric("Total Buys", agg_total_buys)
    c3.metric("Total Sells", agg_total_sells)
    c4.metric("Total Invested", f"${agg_total_invested:.2f}")
    
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Realized P&L", f"${agg_realized_pnl:+.4f}")
    c6.metric("Unrealized P&L", f"${agg_unrealized_pnl:+.4f}")
    total_pnl = agg_realized_pnl + agg_unrealized_pnl
    c7.metric("Total P&L", f"${total_pnl:+.4f}")
    win_rate = (agg_total_wins / (agg_total_wins + agg_total_losses) * 100) if (agg_total_wins + agg_total_losses) > 0 else 0
    c8.metric("Win Rate", f"{win_rate:.1f}%")
    
    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Wins", agg_total_wins)
    c10.metric("Losses", agg_total_losses)
    c11.metric("Open Positions", agg_open_positions)
    c12.metric("Closed Positions", agg_closed_positions)
    
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Best Trade", f"${agg_best_trade_pnl:+.4f}")
        st.caption(agg_best_trade_desc)
    with col2:
        st.metric("Worst Trade", f"${agg_worst_trade_pnl:+.4f}")
        st.caption(agg_worst_trade_desc)
    
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
    
    st.progress(min(1.0, budget_used / 100), text=f"{budget_used:.0f}% of budget")
    
    st.divider()
    st.subheader("Positions by Tier")
    
    tier_counts = {"longshot": 0, "mid": 0, "highprob": 0}
    tier_cost = {"longshot": 0.0, "mid": 0.0, "highprob": 0.0}
    tier_value = {"longshot": 0.0, "mid": 0.0, "highprob": 0.0}
    
    positions = _query(db_path, "SELECT * FROM ev_positions WHERE status='open'")
    for p in positions:
        tier = _guess_tier(p["entry_price"])
        tier_counts[tier] += 1
        tier_cost[tier] += p["cost_basis_usd"]
        tier_value[tier] += p["current_price"] * p["contracts"]
    
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
    st.header("📈 Performance")
    
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
    else:
        st.info("No open positions.")


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

