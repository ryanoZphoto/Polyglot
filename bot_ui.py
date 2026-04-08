"""
bot_ui.py  –  Purpose-built control center for the Polymarket arbitrage bot.

Run with:  streamlit run bot_ui.py
"""
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

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "bot_runtime.log"
DB_PATH = ROOT / os.getenv("BOT_STATE_DB_PATH", "polymarket_bot_state.sqlite3")
REPORT_PATH = ROOT / os.getenv("BOT_ANALYSIS_LOG_PATH", "bot_cycle_report.jsonl")
ENV_PATH = ROOT / ".env"

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polymarket Bot — Command Center",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container{padding-top:0.6rem;padding-bottom:0.6rem;}
h1{font-size:1.5rem;margin-bottom:0;}
h2{font-size:1.15rem;margin-top:0.4rem;margin-bottom:0.1rem;}
h3{font-size:1rem;margin-top:0.3rem;margin-bottom:0;}
.stMetric label{font-size:0.72rem;}
.stMetric [data-testid="stMetricValue"]{font-size:1.2rem;}
div[data-testid="stExpander"] summary{font-size:0.9rem;}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════

def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _db() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _q1(conn: sqlite3.Connection, sql: str, p: tuple = ()) -> sqlite3.Row | None:
    try:
        return conn.execute(sql, p).fetchone()
    except sqlite3.OperationalError:
        return None


def _qa(conn: sqlite3.Connection, sql: str, p: tuple = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, p).fetchall()
    except sqlite3.OperationalError:
        return []


def _latest_report() -> dict[str, Any] | None:
    if not REPORT_PATH.exists():
        return None
    for raw in reversed(REPORT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _log_tail(n: int = 200) -> list[str]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def _parse_log_line(raw: str) -> dict[str, str]:
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            return {
                "ts": str(obj.get("ts", "")),
                "level": str(obj.get("level", "INFO")),
                "msg": str(obj.get("message", raw)),
            }
        except json.JSONDecodeError:
            pass
    return {"ts": "", "level": "INFO", "msg": raw}


def _read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env(updates: dict[str, str]) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("\n".join(f"{k}={v}" for k, v in updates.items()) + "\n", encoding="utf-8")
        return
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    written: set[str] = set()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            written.add(k)
    for k, v in updates.items():
        if k not in written:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_running(proc: Any) -> bool:
    return proc is not None and proc.poll() is None


def _start_bot(env: dict[str, str]) -> subprocess.Popen:
    e = os.environ.copy()
    e.update(env)
    lf = open(LOG_PATH, "a", encoding="utf-8")
    lf.write(f"\n[{_utc()}] ── Bot started from UI ──\n")
    lf.flush()
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(
        [sys.executable, "-m", "polymarket_bot.main"],
        cwd=str(ROOT),
        env=e,
        stdout=lf,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=flags,
    )


def _stop_bot(proc: Any) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# session state init
# ══════════════════════════════════════════════════════════════════════════════

def _init() -> None:
    defaults = {
        "proc": None,
        "started_at": None,
        "notice": "",
        "auto_refresh": False,
        "tab": "Intelligence",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ══════════════════════════════════════════════════════════════════════════════
# data loading
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3)
def _load_data() -> dict[str, Any]:
    report = _latest_report() or {}
    conn = _db()
    data: dict[str, Any] = {
        "report": report,
        "cycles": 0,
        "trades": 0,
        "open_exposure": 0.0,
        "daily_pnl": 0.0,
        "recent_cycles": [],
        "recent_trades": [],
        "near_misses": [],
        "pnl_series": [],
        "auto_tune_log": [],
    }
    if conn is None:
        return data
    try:
        r = _q1(conn, "SELECT COUNT(*) c FROM cycles")
        data["cycles"] = int(r["c"]) if r else 0
        r = _q1(conn, "SELECT COUNT(*) c FROM trades")
        data["trades"] = int(r["c"]) if r else 0
        r = _q1(conn, "SELECT COALESCE(SUM(cost_usd),0) e FROM trades WHERE status IN ('submitted','partially_filled')")
        data["open_exposure"] = float(r["e"]) if r else 0.0
        today = datetime.now(timezone.utc).date().isoformat()
        r = _q1(conn, "SELECT COALESCE(SUM(realized_pnl_usd),0) p FROM fills WHERE substr(created_at,1,10)=?", (today,))
        data["daily_pnl"] = float(r["p"]) if r else 0.0
        data["recent_cycles"] = [dict(x) for x in _qa(conn,
            "SELECT started_at,scanned_markets,opportunities,executed FROM cycles ORDER BY started_at DESC LIMIT 60")]
        data["recent_trades"] = [dict(x) for x in _qa(conn,
            "SELECT trade_id,group_key,mode,status,cost_usd,expected_profit_usd,created_at FROM trades ORDER BY created_at DESC LIMIT 20")]
        # near misses from latest cycle
        r = _q1(conn, "SELECT cycle_id FROM cycles ORDER BY started_at DESC LIMIT 1")
        if r:
            cid = r["cycle_id"]
            data["near_misses"] = [dict(x) for x in _qa(conn,
                "SELECT rank_index,group_key,legs_considered,sum_ask,edge,edge_gap,estimated_profit_usd,profit_gap_usd "
                "FROM near_misses WHERE cycle_id=? ORDER BY rank_index", (cid,))]
        data["pnl_series"] = [dict(x) for x in _qa(conn,
            "SELECT created_at,realized_pnl_usd FROM fills ORDER BY created_at ASC LIMIT 500")]
    finally:
        conn.close()

    # parse auto-tune lines from log
    tune_lines = []
    for raw in _log_tail(500):
        p = _parse_log_line(raw)
        if "Auto-tune applied" in p["msg"]:
            tune_lines.append({"ts": p["ts"], "msg": p["msg"]})
    data["auto_tune_log"] = tune_lines[-20:]
    return data


# ══════════════════════════════════════════════════════════════════════════════
# translation helpers  (numbers → plain English)
# ══════════════════════════════════════════════════════════════════════════════

def _edge_plain(ppm: int | float | None) -> str:
    """Convert edge_ppm to a plain-English sentence."""
    if ppm is None or ppm <= -999_000:
        return "No data yet."
    edge = ppm / 1_000_000.0
    if edge >= 0:
        return f"✅ POSITIVE edge of {edge:.4f} — this would trigger a trade."
    pct = abs(edge) * 100
    if pct < 5:
        return f"🟡 Very close — market needs to shift just {pct:.1f}% tighter for arb."
    if pct < 20:
        return f"🟠 Moderate gap — prices need to converge by {pct:.1f}% for arb."
    return f"🔴 Deep gap — prices are {pct:.0f}% away from profitable arb. Market is very efficient right now."


def _near_miss_plain(row: dict) -> str:
    eg = float(row.get("edge_gap", 0))
    pg = float(row.get("profit_gap_usd", 0))
    legs = int(row.get("legs_considered", 0))
    if eg < 0.005 and pg < 0.05:
        return f"🔥 ALMOST — {legs} legs, needs {eg:.4f} more edge and ${pg:.3f} more profit."
    if eg < 0.05:
        return f"🟡 Close — needs edge to shrink by {eg:.4f} more."
    return f"⬜ Far — edge gap {eg:.4f}, profit gap ${pg:.3f}."


def _strategy_what_is(name: str) -> str:
    desc = {
        "NO-Basket": (
            "Buys NO on every outcome in a multi-outcome market (e.g. 'who wins NBA?'). "
            "Since exactly one team wins, n-1 NOs resolve profitable. "
            "Profit = payout - sum of NO ask prices."
        ),
        "Binary Pair": (
            "Buys both YES and NO on the same market. If YES_price + NO_price < 1.0, "
            "you're guaranteed $1 payout for less than $1 cost."
        ),
        "Multi-Outcome": (
            "Buys all outcome tokens in a market with 3+ outcomes. "
            "Exactly one pays out $1. Profitable when sum of all ask prices < $1."
        ),
        "Leader-Follow": (
            "Watches a high-volume wallet and copies its trades within a time window. "
            "Bets that the leader has better information than the current market price."
        ),
    }
    return desc.get(name, "")


def _leader_pipeline_plain(ld: dict) -> list[tuple[str, str]]:
    rows = int(ld.get("activity_rows", 0))
    sigs = int(ld.get("signals_after_filters", 0))
    miss = int(ld.get("signal_market_miss", 0))
    built = int(ld.get("opportunities_built", 0))
    steps = [
        (f"API returned {rows} recent trades from leader wallet",
         "✅" if rows > 0 else "⚠️ wallet has no recent trades"),
        (f"{sigs} signals passed age/notional/side filters",
         "✅" if sigs > 0 else "⚠️ All filtered — check age window & min notional"),
        (f"{sigs - miss} signals matched to an open market",
         "✅" if (sigs - miss) > 0 else "⚠️ Markets are closed/settled" if miss == sigs and sigs > 0 else "—"),
        (f"{built} opportunities built and sent to scorer",
         "✅" if built > 0 else "—"),
    ]
    return steps


# ══════════════════════════════════════════════════════════════════════════════
# sidebar: bot control + env override
# ══════════════════════════════════════════════════════════════════════════════

env = _read_env()
proc = st.session_state.proc
running = _is_running(proc)

with st.sidebar:
    st.title("⚡ Bot Command Center")
    if running:
        st.success(f"🟢 Running  (PID {proc.pid})")
    else:
        st.error("🔴 Stopped")

    if st.button("▶  Start Bot", disabled=running, width="stretch"):
        st.session_state.proc = _start_bot(env)
        st.session_state.started_at = time.time()
        st.session_state.notice = "Bot started."
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⏹  Stop", disabled=not running, width="stretch"):
            _stop_bot(proc)
            st.session_state.proc = None
            st.session_state.started_at = None
            st.session_state.notice = "Bot stopped."
            st.rerun()
    with col2:
        if st.button("🚨 E-STOP", type="primary", width="stretch"):
            _write_env({"BOT_EMERGENCY_STOP": "true"})
            _stop_bot(proc)
            st.session_state.proc = None
            st.session_state.notice = "EMERGENCY STOP activated."
            st.rerun()

    if env.get("BOT_EMERGENCY_STOP") == "true":
        st.error("⚠️ EMERGENCY STOP is ON")
        if st.button("Clear Emergency Stop", width="stretch"):
            _write_env({"BOT_EMERGENCY_STOP": "false"})
            st.session_state.notice = "Emergency stop cleared."
            st.rerun()

    if st.session_state.notice:
        st.info(st.session_state.notice)
    if st.session_state.started_at:
        up = int(time.time() - st.session_state.started_at)
        st.caption(f"Up {up//60}m {up%60}s")

    st.divider()
    # Quick .env overrides the user can flip without leaving the sidebar
    st.subheader("Quick Settings")
    mode = env.get("BOT_MODE", "dry_run")
    new_mode = st.selectbox("Mode", ["dry_run", "live"], index=0 if mode == "dry_run" else 1)
    estop = env.get("BOT_EMERGENCY_STOP", "false") == "true"

    scan_lim = st.number_input("Scan limit (markets)", 50, 1000, int(env.get("BOT_SCAN_LIMIT", "400")), step=50)
    min_liq = st.number_input("Min liquidity ($)", 0, 50000, int(env.get("BOT_MIN_LIQUIDITY", "500")), step=100)
    min_edge = st.number_input("Min edge (arb scanner)", 0.0001, 0.10, float(env.get("BOT_MIN_EDGE", "0.002")), step=0.0005, format="%.4f")
    min_profit = st.number_input("Min profit/cycle ($)", 0.01, 20.0, float(env.get("BOT_MIN_PROFIT_USD", "0.10")), step=0.05, format="%.2f")
    aggression = st.slider("Aggression (auto-tune target)", 0.50, 1.00, float(env.get("BOT_AGGRESSION", "0.92")), step=0.01)
    min_net_profit = st.number_input("Min NET profit ($)", 0.01, 5.0, float(env.get("BOT_MIN_NET_PROFIT_USD", "0.20")), step=0.05, format="%.2f")
    min_net_edge = st.number_input("Min NET edge", 0.0005, 0.02, float(env.get("BOT_MIN_NET_EDGE", "0.004")), step=0.0005, format="%.4f")

    if st.button("💾 Save Settings & Restart", width="stretch"):
        _write_env({
            "BOT_MODE": new_mode,
            "BOT_SCAN_LIMIT": str(scan_lim),
            "BOT_MIN_LIQUIDITY": str(min_liq),
            "BOT_MIN_EDGE": f"{min_edge:.4f}",
            "BOT_MIN_PROFIT_USD": f"{min_profit:.2f}",
            "BOT_AGGRESSION": f"{aggression:.2f}",
            "BOT_MIN_NET_PROFIT_USD": f"{min_net_profit:.2f}",
            "BOT_MIN_NET_EDGE": f"{min_net_edge:.4f}",
        })
        _stop_bot(proc)
        time.sleep(0.4)
        new_env = _read_env()
        st.session_state.proc = _start_bot(new_env)
        st.session_state.started_at = time.time()
        st.session_state.notice = "Settings saved and bot restarted."
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.session_state.auto_refresh = st.checkbox("Auto-refresh (3s)", value=st.session_state.auto_refresh)


# ══════════════════════════════════════════════════════════════════════════════
# data
# ══════════════════════════════════════════════════════════════════════════════

data = _load_data()
report = data["report"]
diag = report.get("diagnostics", {})
stage = report.get("stage_diagnostics", {})
ld = report.get("leader_diagnostics", {})
thresholds = report.get("thresholds", {})
summary = report.get("summary", {})

# ══════════════════════════════════════════════════════════════════════════════
# top status bar
# ══════════════════════════════════════════════════════════════════════════════

st.title("Polymarket Arbitrage Bot — Command Center")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Status", "🟢 Running" if running else "🔴 Stopped")
c2.metric("Mode", env.get("BOT_MODE", "dry_run").upper())
c3.metric("Total Cycles", data["cycles"])
c4.metric("Open Exposure", f"${data['open_exposure']:.2f}")
c5.metric("Today's PnL", f"${data['daily_pnl']:.2f}")
c6.metric("Total Trades", data["trades"])

# ══════════════════════════════════════════════════════════════════════════════
# tabs
# ══════════════════════════════════════════════════════════════════════════════

tab_intel, tab_radar, tab_strategies, tab_leader, tab_autotune, tab_perf, tab_config, tab_logs = st.tabs([
    "🧠 Intelligence",
    "📡 Market Radar",
    "⚙️ Strategies",
    "👁️ Leader-Follow",
    "🔧 Auto-Tune",
    "📈 Performance",
    "🗂️ Full Config",
    "📋 Logs",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: Intelligence — plain-English explanation of what's happening
# ══════════════════════════════════════════════════════════════════════════════

with tab_intel:
    st.header("🧠 What is the bot doing right now?")

    scanned = int(summary.get("scanned_markets", 0))
    groups = int(summary.get("eligible_groups", 0))
    selected = int(summary.get("selected_opportunities", 0))
    executed = int(summary.get("executed", 0))
    near = int(summary.get("near_misses", 0))

    if not report:
        st.info("No cycle has completed yet. Start the bot to see data here.")
    else:
        st.markdown(f"""
**Last completed cycle:**
- Scanned **{scanned} markets** → grouped into **{groups} candidate groups**
- Found **{selected} tradeable opportunities** → executed **{executed}**
- Tracked **{near} near-miss candidates** (markets that almost qualified)
""")
        # ── what does "0 opportunities" mean? ──
        if selected == 0:
            st.warning("""
**Why 0 opportunities?**

The bot checks whether buying a basket of positions guarantees a profit after fees.
For that to work, the prices paid across all legs must be *less than* the guaranteed payout.

Right now, the market makers on Polymarket are pricing everything efficiently —
the cost of every basket is higher than what you'd receive at resolution.
This is completely normal. Opportunities appear briefly when:
- A big trade moves the price of one leg
- A market maker adjusts slowly
- News breaks and the book is temporarily thin

The bot runs every ~10 seconds and will catch it when it happens.
""")
        else:
            st.success(f"Bot found {selected} opportunities and executed {executed}. Check Performance tab for fills.")

        st.divider()
        st.subheader("Market Efficiency Readings (this cycle)")
        st.caption("How far the best-priced basket is from profitability. Negative = not there yet. Positive = trade found.")

        nb_ppm = diag.get("no_basket_best_edge_ppm", -1_000_000)
        pr_ppm = diag.get("pair_best_edge_ppm", -1_000_000)
        ml_ppm = diag.get("multi_best_edge_ppm", -1_000_000)

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**NO-Basket strategy**")
            st.markdown(_edge_plain(nb_ppm))
            nb_edge = nb_ppm / 1_000_000 if nb_ppm > -1_000_000 else -1.0
            bar_val = max(0.0, min(1.0, (nb_edge + 1.0) / 1.0))
            st.progress(bar_val, text=f"Edge: {nb_edge:.4f}")

        with col_b:
            st.markdown("**Binary Pair strategy**")
            st.markdown(_edge_plain(pr_ppm))
            pr_edge = pr_ppm / 1_000_000 if pr_ppm > -1_000_000 else -1.0
            bar_val = max(0.0, min(1.0, (pr_edge + 1.0) / 1.0))
            st.progress(bar_val, text=f"Edge: {pr_edge:.4f}")

        with col_c:
            st.markdown("**Multi-Outcome strategy**")
            st.markdown(_edge_plain(ml_ppm))
            ml_edge = ml_ppm / 1_000_000 if ml_ppm > -1_000_000 else -1.0
            bar_val = max(0.0, min(1.0, (ml_edge + 1.0) / 1.0))
            st.progress(bar_val, text=f"Edge: {ml_edge:.4f}")

        st.divider()
        st.subheader("Cycle Funnel")
        st.caption("Each row shows how many candidates survived each filter stage.")

        elig = int(diag.get("eligible_markets", 0))
        grp_total = int(diag.get("groups_total", 0))
        grp_small = int(diag.get("groups_below_min_size", 0))
        nb_eval = int(diag.get("no_basket_evaluated", 0))
        nb_found = int(diag.get("no_basket_found", 0))
        pr_eval = int(diag.get("pair_evaluated", 0))
        pr_found = int(diag.get("pair_found", 0))

        funnel = [
            ("Markets fetched from Polymarket API", scanned, "Start: raw market list"),
            ("Markets meeting liquidity & activity rules", elig, f"Filtered by min_liquidity=${min_liq}"),
            ("Event groups formed", grp_total, "Grouped by event slug for NO-basket"),
            ("Groups large enough to evaluate", grp_total - grp_small, f"Need at least {env.get('BOT_MIN_GROUP_SIZE','2')} legs"),
            ("NO-basket groups evaluated", nb_eval, "Edge calculation performed"),
            ("Binary pairs evaluated", pr_eval, "YES+NO cost vs $1 payout"),
            ("Opportunities found (pre-score)", nb_found + pr_found, "Passed min_edge + min_profit filter"),
            ("Opportunities selected (post-score)", selected, "Passed net profit & net edge after fees/slippage"),
            ("Executed", executed, "Risk checks passed"),
        ]

        for label, count, note in funnel:
            col_l, col_n, col_note = st.columns([4, 1, 4])
            col_l.write(label)
            col_n.write(f"**{count}**")
            col_note.caption(note)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: Market Radar — near-miss details
# ══════════════════════════════════════════════════════════════════════════════

with tab_radar:
    st.header("📡 Market Radar — Near-Miss Candidates")
    st.caption("These are the markets that came closest to triggering a trade. Use these to understand where the market is and what needs to shift.")

    nm_list = data["near_misses"]
    top_report = report.get("top_near_misses", [])

    if not nm_list and not top_report:
        st.info("No near-miss data yet. Run at least one full cycle.")
    else:
        use = nm_list if nm_list else top_report
        for i, row in enumerate(use[:8]):
            group = row.get("group_key", "unknown")
            legs = row.get("legs_considered", "?")
            edge = float(row.get("edge", 0))
            eg = float(row.get("edge_gap", 0))
            pg = float(row.get("profit_gap_usd", 0))
            est_profit = float(row.get("estimated_profit_usd", 0))

            with st.expander(f"#{i+1}  {group}  —  {_near_miss_plain(row)}", expanded=(i == 0)):
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Legs (positions)", legs)
                col2.metric("Current Edge", f"{edge:.4f}")
                col3.metric("Edge Gap (how much closer needed)", f"{eg:.4f}")
                col4.metric("Profit Gap", f"${pg:.3f}")

                st.markdown(f"""
**What this means in plain English:**

This group has **{legs} positions** the bot is considering. The current combined ask price
produces an edge of **{edge:.4f}**. To trigger a trade, that edge needs to reach
the scanner threshold.

- The edge needs to improve by **{eg:.4f}** more.
- Estimated profit at current prices would be **${est_profit:.3f}** — needs to be ${float(row.get('min_profit_required', 0)):.3f}+.
- This happens when market makers lower their ask prices on one or more legs,
  or when a large order moves the book temporarily.
""")
                if eg < 0.01:
                    st.success("🔥 This is a HOT candidate — could trigger on the next cycle if one leg moves slightly.")
                elif eg < 0.05:
                    st.warning("🟡 Warm candidate — needs a small shift in at least one market.")
                else:
                    st.info("⬜ Cool candidate — needs meaningful price movement across legs.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: Strategies — what each one does and its current thresholds
# ══════════════════════════════════════════════════════════════════════════════

with tab_strategies:
    st.header("⚙️ Strategy Breakdown")

    strategies_info = [
        {
            "name": "NO-Basket",
            "enabled_key": "BOT_ENABLE_NO_BASKET_STRATEGY",
            "min_edge_key": "BOT_MIN_EDGE",
            "min_profit_key": "BOT_MIN_PROFIT_USD",
            "diag_found": int(diag.get("no_basket_found", 0)),
            "diag_eval": int(diag.get("no_basket_evaluated", 0)),
            "best_edge_ppm": diag.get("no_basket_best_edge_ppm"),
        },
        {
            "name": "Binary Pair",
            "enabled_key": "BOT_ENABLE_BINARY_PAIR_STRATEGY",
            "min_edge_key": "BOT_PAIR_MIN_EDGE",
            "min_profit_key": "BOT_PAIR_MIN_PROFIT_USD",
            "diag_found": int(diag.get("pair_found", 0)),
            "diag_eval": int(diag.get("pair_evaluated", 0)),
            "best_edge_ppm": diag.get("pair_best_edge_ppm"),
        },
        {
            "name": "Multi-Outcome",
            "enabled_key": "BOT_ENABLE_MULTI_OUTCOME_STRATEGY",
            "min_edge_key": "BOT_MULTI_MIN_EDGE",
            "min_profit_key": "BOT_MULTI_MIN_PROFIT_USD",
            "diag_found": int(diag.get("multi_found", 0)),
            "diag_eval": int(diag.get("multi_evaluated", 0)),
            "best_edge_ppm": diag.get("multi_best_edge_ppm"),
        },
    ]

    for s in strategies_info:
        enabled = env.get(s["enabled_key"], "true").lower() == "true"
        with st.expander(
            f"{'✅' if enabled else '❌'} {s['name']} Strategy",
            expanded=True,
        ):
            st.markdown(_strategy_what_is(s["name"]))
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Status", "ON" if enabled else "OFF")
            col2.metric("Evaluated last cycle", s["diag_eval"])
            col3.metric("Found last cycle", s["diag_found"])
            ppm = s["best_edge_ppm"]
            be = (ppm / 1_000_000) if ppm and ppm > -1_000_000 else None
            col4.metric("Best edge seen", f"{be:.4f}" if be is not None else "n/a")

            st.markdown(f"""
**Current thresholds** (from .env):
- Min scanner edge: `{env.get(s['min_edge_key'], '?')}`
- Min scanner profit: `${env.get(s['min_profit_key'], '?')}`
- Min net profit (after fees): `${env.get('BOT_MIN_NET_PROFIT_USD', '?')}`
- Min net edge (after fees): `{env.get('BOT_MIN_NET_EDGE', '?')}`
""")
            # toggle
            new_en = st.toggle(f"Enable {s['name']}", value=enabled, key=f"tog_{s['name']}")
            if new_en != enabled:
                _write_env({s["enabled_key"]: "true" if new_en else "false"})
                st.session_state.notice = f"{s['name']} {'enabled' if new_en else 'disabled'}. Restart bot to apply."
                st.rerun()

    st.divider()
    st.subheader("Scoring thresholds (applied AFTER strategies find candidates)")
    st.markdown(f"""
Even if a strategy finds an opportunity, the **profit scorer** deducts:
- **Taker fee**: `{env.get('BOT_TAKER_FEE_RATE','0.03')}` × (shares × price per leg)
- **Slippage**: base `{env.get('BOT_SLIPPAGE_BPS_BASE','5')}` bps + `{env.get('BOT_SLIPPAGE_BPS_PER_LEG','2')}` bps/leg + illiquidity premium
- **Latency penalty**: `{env.get('BOT_LATENCY_PENALTY_BPS','6')}` bps
- **Risk buffer**: `{env.get('BOT_RISK_BUFFER_BPS','10')}` bps

After those deductions, the trade must still clear:
- **Min net profit**: `${env.get('BOT_MIN_NET_PROFIT_USD','0.20')}`
- **Min net edge**: `{env.get('BOT_MIN_NET_EDGE','0.004')}`

The **aggression** setting (`{env.get('BOT_AGGRESSION','0.92')}`) scales these thresholds down — at aggression=1.0 they drop to zero.
""")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: Leader-Follow
# ══════════════════════════════════════════════════════════════════════════════

with tab_leader:
    st.header("👁️ Leader-Follow Strategy")

    st.markdown(_strategy_what_is("Leader-Follow"))

    st.divider()
    enabled_lf = env.get("BOT_ENABLE_LEADER_FOLLOW", "false").lower() == "true"
    wallet = env.get("BOT_LEADER_WALLET", "")
    st.markdown(f"""
**Configuration:**
- Enabled: `{'YES' if enabled_lf else 'NO'}`
- Watching wallet: `{wallet or 'not set'}`
- Max signal age: `{env.get('BOT_LEADER_MAX_SIGNAL_AGE_SECONDS','1800')}` seconds
- Min notional: `${env.get('BOT_LEADER_MIN_NOTIONAL_USD','20')}`
- Buy-side only: `{env.get('BOT_LEADER_REQUIRE_BUY_SIDE','false')}`
- Price tolerance: `{env.get('BOT_LEADER_PRICE_TOLERANCE_BPS','120')}` bps (how much price can drift before rejecting)
- Alpha (probability boost applied): `{env.get('BOT_LEADER_ALPHA','0.04')}`
""")

    st.divider()
    st.subheader("Signal Pipeline — Last Cycle")

    if ld:
        steps = _leader_pipeline_plain(ld)
        for step_text, status in steps:
            st.write(f"{status} {step_text}")

        st.divider()
        st.subheader("Full Leader Diagnostics")
        col1, col2 = st.columns(2)
        with col1:
            for k in ["activity_rows", "signals_after_filters", "markets_loaded", "opportunities_built"]:
                st.metric(k.replace("_", " ").title(), ld.get(k, 0))
        with col2:
            for k in ["signal_market_miss", "signal_rejected_cooldown", "signal_rejected_price_drift", "signal_rejected_non_positive_edge"]:
                st.metric(k.replace("signal_rejected_", "rejected: ").replace("signal_", "").replace("_", " ").title(), ld.get(k, 0))
    else:
        st.info("No leader-follow data yet — enable the strategy and run a cycle.")

    st.divider()
    st.subheader("Edit Leader Settings")
    new_wallet = st.text_input("Leader wallet address", value=wallet)
    new_age = st.number_input("Max signal age (seconds)", 60, 86400, int(env.get("BOT_LEADER_MAX_SIGNAL_AGE_SECONDS", "1800")), step=60)
    new_notional = st.number_input("Min notional ($)", 1.0, 1000.0, float(env.get("BOT_LEADER_MIN_NOTIONAL_USD", "20")), step=5.0)
    new_buy_only = st.checkbox("Buy-side only", value=env.get("BOT_LEADER_REQUIRE_BUY_SIDE", "false").lower() == "true")
    new_tol = st.number_input("Price tolerance (bps)", 0, 500, int(env.get("BOT_LEADER_PRICE_TOLERANCE_BPS", "120")), step=10)
    new_alpha = st.number_input("Alpha (probability boost)", 0.0, 0.15, float(env.get("BOT_LEADER_ALPHA", "0.04")), step=0.005, format="%.3f")
    if st.button("Save Leader Settings"):
        _write_env({
            "BOT_LEADER_WALLET": new_wallet.strip().lower(),
            "BOT_LEADER_MAX_SIGNAL_AGE_SECONDS": str(new_age),
            "BOT_LEADER_MIN_NOTIONAL_USD": str(new_notional),
            "BOT_LEADER_REQUIRE_BUY_SIDE": "true" if new_buy_only else "false",
            "BOT_LEADER_PRICE_TOLERANCE_BPS": str(new_tol),
            "BOT_LEADER_ALPHA": f"{new_alpha:.3f}",
        })
        st.session_state.notice = "Leader settings saved. Restart bot to apply."
        st.rerun()

    st.divider()
    st.subheader("Why does 'signal_market_miss' happen?")
    st.markdown("""
When the leader wallet trades a market, the bot looks that market up in its active-market cache.
If `signal_market_miss > 0`, it means those markets are **not currently in the active list** — usually because:

1. **The game/event already ended** — sports markets settle within hours of the game. Today's NBA/MLB games are already closed.
2. **The market was delisted** — rarely happens but possible.
3. **Token ID format mismatch** — the activity API returns very long numeric token IDs that need to match exactly.

**Solution:** The leader wallet needs to be trading in markets that are still **open and accepting orders**.
For sports, that means following trades placed *during* the game, not after settlement.
For political/crypto markets, the window is much longer.
""")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: Auto-Tune
# ══════════════════════════════════════════════════════════════════════════════

with tab_autotune:
    st.header("🔧 Auto-Tune Status")
    st.markdown("""
The auto-tuner adjusts **aggression**, **min_net_profit**, and **min_net_edge** every N cycles
based on what happened:

| Situation | Action |
|---|---|
| No opportunities found at all | Loosen filters (raise aggression, lower thresholds) |
| Candidates found but allocator rejected all | Loosen modestly |
| Good fills with strong net profit | Tighten slightly to maintain quality |
| Top candidates are net negative after fees | Tighten to avoid bad fills |
""")

    st.divider()
    col1, col2, col3 = st.columns(3)
    col1.metric("Current Aggression", f"{float(env.get('BOT_AGGRESSION','0.92')):.2f}",
                help="0=conservative, 1=take everything that's slightly positive")
    col2.metric("Current Min Net Profit", f"${float(env.get('BOT_MIN_NET_PROFIT_USD','0.20')):.2f}")
    col3.metric("Current Min Net Edge", f"{float(env.get('BOT_MIN_NET_EDGE','0.004')):.4f}")

    col4, col5, col6 = st.columns(3)
    col4.metric("Interval (cycles)", env.get("BOT_AUTO_TUNE_INTERVAL_CYCLES", "3"))
    col5.metric("Min aggression allowed", env.get("BOT_AUTO_TUNE_MIN_AGGRESSION", "0.70"))
    col6.metric("Max aggression allowed", env.get("BOT_AUTO_TUNE_MAX_AGGRESSION", "0.98"))

    st.divider()
    st.subheader("Recent Auto-Tune Adjustments (from log)")
    tune_log = data["auto_tune_log"]
    if tune_log:
        for entry in reversed(tune_log):
            st.caption(f"{entry['ts']}  {entry['msg']}")
    else:
        st.info("No auto-tune events logged yet.")

    st.divider()
    st.subheader("Manual Override")
    st.caption("Use these if you want to force-set the current values without waiting for auto-tune.")
    m_agg = st.slider("Force aggression to", 0.50, 1.00, float(env.get("BOT_AGGRESSION","0.92")), step=0.01)
    m_profit = st.number_input("Force min net profit ($)", 0.01, 5.0, float(env.get("BOT_MIN_NET_PROFIT_USD","0.20")), step=0.05, format="%.2f")
    m_edge = st.number_input("Force min net edge", 0.0005, 0.02, float(env.get("BOT_MIN_NET_EDGE","0.004")), step=0.0005, format="%.4f")
    if st.button("Apply Manual Override"):
        _write_env({
            "BOT_AGGRESSION": f"{m_agg:.2f}",
            "BOT_MIN_NET_PROFIT_USD": f"{m_profit:.2f}",
            "BOT_MIN_NET_EDGE": f"{m_edge:.4f}",
        })
        st.session_state.notice = "Auto-tune values overridden. Restart bot to apply."
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6: Performance
# ══════════════════════════════════════════════════════════════════════════════

with tab_perf:
    st.header("📈 Performance")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Cycles", data["cycles"])
    c2.metric("Today's Realized PnL", f"${data['daily_pnl']:.4f}")
    c3.metric("Open Exposure", f"${data['open_exposure']:.2f}")

    pnl = data["pnl_series"]
    if pnl:
        running_total = 0.0
        chart_data = []
        for row in pnl:
            running_total += float(row["realized_pnl_usd"])
            chart_data.append({"time": row["created_at"], "cumulative_pnl_usd": running_total})
        st.subheader("Cumulative PnL")
        st.line_chart(chart_data, x="time", y="cumulative_pnl_usd")
    else:
        st.info("No fills recorded yet. Trades execute in dry-run mode but fills appear once the bot finds qualifying opportunities.")

    rc = data["recent_cycles"]
    if rc:
        st.subheader("Cycle History")
        st.line_chart(
            list(reversed(rc))[:60],
            x="started_at",
            y=["scanned_markets", "opportunities", "executed"],
        )

    rt = data["recent_trades"]
    if rt:
        st.subheader("Recent Trades")
        st.dataframe(rt, width="stretch", hide_index=True)
    else:
        st.info("No trades recorded yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7: Full Config editor
# ══════════════════════════════════════════════════════════════════════════════

with tab_config:
    st.header("🗂️ Full .env Configuration Editor")
    st.caption("Edit any setting. Changes are written to .env and take effect on next bot restart.")

    current = _read_env()
    sections = {
        "Core": ["BOT_MODE", "BOT_SCAN_LIMIT", "BOT_SPORTS_ONLY", "BOT_EMERGENCY_STOP", "BOT_POLL_INTERVAL_SECONDS"],
        "Filters": ["BOT_MIN_LIQUIDITY", "BOT_MIN_EDGE", "BOT_MIN_PROFIT_USD", "BOT_MIN_GROUP_SIZE", "BOT_MAX_GROUP_SIZE"],
        "Capital & Risk": ["BOT_MAX_CAPITAL_PER_TRADE", "BOT_MAX_BUNDLE_SHARES", "BOT_MAX_OPPS_PER_CYCLE",
                           "BOT_MAX_OPEN_EXPOSURE_USD", "BOT_MAX_EVENT_EXPOSURE_USD", "BOT_MAX_DAILY_LOSS_USD",
                           "BOT_MAX_ORDERS_PER_MINUTE", "BOT_MARKET_COOLDOWN_SECONDS"],
        "Scoring": ["BOT_AGGRESSION", "BOT_MIN_NET_PROFIT_USD", "BOT_MIN_NET_EDGE", "BOT_TAKER_FEE_RATE",
                    "BOT_SLIPPAGE_BPS_BASE", "BOT_SLIPPAGE_BPS_PER_LEG", "BOT_SLIPPAGE_BPS_ILLIQUIDITY",
                    "BOT_LATENCY_PENALTY_BPS", "BOT_RISK_BUFFER_BPS"],
        "Strategies": ["BOT_ENABLE_NO_BASKET_STRATEGY", "BOT_ENABLE_BINARY_PAIR_STRATEGY",
                       "BOT_ENABLE_MULTI_OUTCOME_STRATEGY", "BOT_PAIR_MIN_EDGE", "BOT_PAIR_MIN_PROFIT_USD",
                       "BOT_MULTI_MIN_EDGE", "BOT_MULTI_MIN_PROFIT_USD"],
        "Leader-Follow": ["BOT_ENABLE_LEADER_FOLLOW", "BOT_LEADER_WALLET", "BOT_LEADER_MAX_SIGNAL_AGE_SECONDS",
                          "BOT_LEADER_MIN_NOTIONAL_USD", "BOT_LEADER_PRICE_TOLERANCE_BPS",
                          "BOT_LEADER_ALPHA", "BOT_LEADER_MAX_SIGNALS_PER_CYCLE", "BOT_LEADER_REQUIRE_BUY_SIDE"],
        "Auto-Tune": ["BOT_ENABLE_AUTO_TUNE", "BOT_AUTO_TUNE_INTERVAL_CYCLES", "BOT_AUTO_TUNE_AGGRESSION_STEP",
                      "BOT_AUTO_TUNE_MIN_AGGRESSION", "BOT_AUTO_TUNE_MAX_AGGRESSION",
                      "BOT_AUTO_TUNE_MIN_NET_PROFIT_USD", "BOT_AUTO_TUNE_MAX_NET_PROFIT_USD"],
        "Network": ["BOT_REQUEST_TIMEOUT_SECONDS", "BOT_MAX_REQUEST_RETRIES", "BOT_RETRY_BACKOFF_SECONDS",
                    "BOT_MAX_WORKERS", "BOT_MAX_QUOTE_FETCH_LATENCY_MS"],
    }

    edits: dict[str, str] = {}
    for section, keys in sections.items():
        with st.expander(section, expanded=(section in ("Core", "Filters", "Scoring"))):
            for k in keys:
                v = current.get(k, "")
                new_v = st.text_input(k, value=v, key=f"cfg_{k}")
                if new_v != v:
                    edits[k] = new_v

    if edits:
        st.warning(f"You have {len(edits)} unsaved change(s): {', '.join(edits.keys())}")
        if st.button("💾 Save All Changes"):
            _write_env(edits)
            st.session_state.notice = f"Saved {len(edits)} setting(s). Restart bot to apply."
            st.cache_data.clear()
            st.rerun()
    else:
        st.success("No unsaved changes.")

    st.divider()
    st.subheader("Current .env (raw)")
    st.code(ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else "(not found)", language="bash")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8: Logs
# ══════════════════════════════════════════════════════════════════════════════

with tab_logs:
    st.header("📋 Live Process Log")

    n_lines = st.slider("Lines to show", 20, 500, 100, step=20)
    show_level = st.selectbox("Filter level", ["ALL", "INFO", "WARNING", "ERROR"])

    raw_lines = _log_tail(n_lines)
    parsed = [_parse_log_line(l) for l in raw_lines]

    if show_level != "ALL":
        parsed = [p for p in parsed if p["level"].upper() == show_level]

    display = []
    for p in parsed:
        prefix = f"[{p['ts']}] [{p['level']}]  " if p["ts"] else ""
        display.append(prefix + p["msg"])

    st.code("\n".join(display) if display else "No log output yet.", language="text")

    st.divider()
    st.subheader("Cycle Report (Latest JSON)")
    if report:
        st.json(report)
    else:
        st.info("No cycle report yet.")


# ══════════════════════════════════════════════════════════════════════════════
# auto-refresh
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.auto_refresh:
    time.sleep(3)
    st.cache_data.clear()
    st.rerun()
