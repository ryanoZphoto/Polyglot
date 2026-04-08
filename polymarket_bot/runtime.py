from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .auto_tune import AutoTuner
from .config import BotConfig
from .data_client import ResilientDataClient
from .execution import DryRunExecutor, LiveExecutor, OrderExecutor
from .leader_follow import LeaderFollowStrategy
from .llm_ranker import OpportunityRanker
from .profit_scoring import ScoredOpportunity, ProfitScorer
from .reconcile import reconcile_trade
from .risk import RiskEngine
from .scanner import NearMissCandidate, OpportunityScanner, ScanResult
from .state import StateStore
from .strategy_allocator import StrategyAllocator
from .types import ExecutionResult, Opportunity, ParsedMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    cycle_id: str
    scanned_markets: int
    eligible_groups: int
    opportunities: list[Opportunity]
    executions: list[ExecutionResult]
    near_misses: list[NearMissCandidate]
    diagnostics: dict[str, int]
    top_scores: list[ScoredOpportunity]


def _write_cycle_report(
    config: BotConfig,
    summary: RunSummary,
    stage_diagnostics: dict[str, int] | None = None,
    leader_diagnostics: dict[str, int] | None = None,
) -> None:
    path = Path(config.analysis_log_path)
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "cycle_id": summary.cycle_id,
        "mode": config.mode,
        "scan_limit": config.scan_limit,
        "sports_only": config.sports_only,
        "strategies": {
            "no_basket": config.enable_no_basket_strategy,
            "pair": config.enable_binary_pair_strategy,
            "multi_outcome": config.enable_multi_outcome_strategy,
            "leader_follow": config.enable_leader_follow,
        },
        "thresholds": {
            "min_edge": config.min_edge,
            "min_profit_usd": config.min_profit_usd,
            "pair_min_edge": config.pair_min_edge,
            "pair_min_profit_usd": config.pair_min_profit_usd,
            "multi_min_edge": config.multi_min_edge,
            "multi_min_profit_usd": config.multi_min_profit_usd,
            "min_group_size": config.min_group_size,
            "max_group_size": config.max_group_size,
            "aggression": config.aggression,
            "min_net_profit_usd": config.min_net_profit_usd,
            "min_net_edge": config.min_net_edge,
        },
        "summary": {
            "scanned_markets": summary.scanned_markets,
            "eligible_groups": summary.eligible_groups,
            "selected_opportunities": len(summary.opportunities),
            "executed": len(summary.executions),
            "near_misses": len(summary.near_misses),
        },
        "diagnostics": summary.diagnostics,
        "stage_diagnostics": stage_diagnostics or {},
        "leader_diagnostics": leader_diagnostics or {},
        "top_near_misses": [
            {
                "group_key": n.group_key,
                "legs_considered": n.legs_considered,
                "edge": n.edge,
                "edge_gap": n.edge_gap,
                "estimated_profit_usd": n.estimated_profit_usd,
                "profit_gap_usd": n.profit_gap_usd,
            }
            for n in summary.near_misses[:5]
        ],
        "executions": [
            {
                "ok": e.ok,
                "mode": e.mode,
                "trade_id": e.trade_id,
                "message": e.message,
                "errors": e.errors or [],
            }
            for e in summary.executions
        ],
        "top_scores": [
            {
                "group_key": s.opportunity.group_key,
                "gross_profit_usd": round(s.adjusted_expected_profit_usd, 6),
                "fee_usd": round(s.estimated_fee_usd, 6),
                "slippage_usd": round(s.estimated_slippage_usd, 6),
                "latency_usd": round(s.estimated_latency_penalty_usd, 6),
                "risk_buffer_usd": round(s.estimated_risk_buffer_usd, 6),
                "net_profit_usd": round(s.net_profit_usd, 6),
                "net_edge": round(s.net_edge, 8),
                "score": round(s.score, 6),
            }
            for s in summary.top_scores[:5]
        ],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")


def _executor_from_config(config: BotConfig) -> OrderExecutor:
    if config.dry_run:
        return DryRunExecutor()
    if not config.private_key:
        raise ValueError("BOT_MODE=live requires PM_PRIVATE_KEY to be set.")
    if config.signature_type in {1, 2} and not config.funder:
        raise ValueError("PM_SIGNATURE_TYPE=1/2 requires PM_FUNDER to be set.")
    return LiveExecutor(
        clob_host=config.clob_host,
        chain_id=config.chain_id,
        private_key=config.private_key,
        signature_type=config.signature_type,
        api_key=config.api_key,
        api_secret=config.api_secret,
        api_passphrase=config.api_passphrase,
        funder=config.funder,
    )


def run_bot_once(
    config: BotConfig,
    last_trade_at: dict[str, float] | None = None,
    prefetched_markets: list[ParsedMarket] | None = None,
) -> RunSummary:
    if last_trade_at is None:
        last_trade_at = {}

    state = StateStore(config.state_db_path)
    try:
        data_client = ResilientDataClient(config)
        scanner = OpportunityScanner(config, data_client)
        leader_follow = LeaderFollowStrategy(config, data_client)
        ranker = OpportunityRanker(config)
        scorer = ProfitScorer(config)
        allocator = StrategyAllocator(config)
        risk_engine = RiskEngine(config, state)
        executor = _executor_from_config(config)

        cycle_started = time.time()
        if config.enable_arb_scanner:
            scanner_started = time.time()
            # scan() now returns the fetched markets list too so leader_follow can reuse it.
            scan, fetched_markets = scanner.scan(last_trade_at=last_trade_at)
            scanner_ms = int((time.time() - scanner_started) * 1000)
        else:
            scan = ScanResult(
                scanned_markets=0,
                grouped_candidates=0,
                opportunities=[],
                near_misses=[],
                diagnostics={"scanner_disabled": 1},
            )
            fetched_markets = prefetched_markets or []
            scanner_ms = 0

        # Issue F fix: pass already-fetched markets to leader_follow to avoid a second full fetch.
        leader_started = time.time()
        leader_result = leader_follow.build_opportunities(
            last_trade_at=last_trade_at,
            cached_markets=fetched_markets if fetched_markets else None,
        )
        leader_ms = int((time.time() - leader_started) * 1000)
        if leader_result.opportunities:
            logger.info("Leader-follow candidates=%s", len(leader_result.opportunities))
        else:
            logger.info("Leader-follow no candidates diagnostics=%s", leader_result.diagnostics)

        merged_opps = scan.opportunities + leader_result.opportunities
        ranked = ranker.rank(merged_opps)
        scored = scorer.score_many(ranked)
        selected = allocator.select(scored)

        cycle_id = str(uuid.uuid4())
        executions: list[ExecutionResult] = []
        for item in selected:
            opp = item.opportunity
            risk = risk_engine.evaluate(opp)
            if not risk.allowed:
                logger.info("Skipping opportunity group=%s reason=%s", opp.group_key, risk.reason)
                continue

            trade_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cycle_id}:{opp.group_key}:{opp.sum_ask:.8f}"))
            if state.has_trade(trade_id):
                logger.info("Skipping duplicate trade_id=%s", trade_id)
                continue

            state.record_trade(
                trade_id=trade_id,
                cycle_id=cycle_id,
                group_key=opp.group_key,
                event_key=opp.group_key,
                mode=config.mode,
                status="pending",
                cost_usd=opp.total_cost_usd,
                expected_profit_usd=opp.expected_profit_usd,
            )
            result = executor.execute(trade_id=trade_id, opp=opp, state=state)
            executions.append(result)
            reconcile_trade(state, trade_id, opp, result)

            for leg in opp.legs:
                last_trade_at[leg.market_id] = time.time()

        state.record_cycle(
            cycle_id=cycle_id,
            scanned_markets=scan.scanned_markets,
            opportunities=len(selected),
            executed=len(executions),
        )
        state.record_near_misses(cycle_id=cycle_id, near_misses=scan.near_misses)
        state.record_cycle_diagnostics(cycle_id=cycle_id, diagnostics=scan.diagnostics)

        # Performance Calibration Layer (Issue #14): Update marks for previous dry runs.
        if config.dry_run:
            try:
                calibrate_dry_runs(config, state, data_client)
            except Exception as exc:
                logger.warning("calibrate_dry_runs failed (non-fatal): %s", exc)

        summary = RunSummary(
            cycle_id=cycle_id,
            scanned_markets=scan.scanned_markets,
            eligible_groups=scan.grouped_candidates,
            opportunities=[x.opportunity for x in selected],
            executions=executions,
            near_misses=scan.near_misses,
            diagnostics=scan.diagnostics,
            top_scores=scored,
        )
        stage_diagnostics = {
            "scanner_ms": scanner_ms,
            "leader_follow_ms": leader_ms,
            "cycle_total_ms": int((time.time() - cycle_started) * 1000),
            "scanner_candidates": len(scan.opportunities),
            "leader_candidates": len(leader_result.opportunities),
            "merged_candidates": len(merged_opps),
            "ranked_candidates": len(ranked),
            "scored_candidates": len(scored),
            "selected_candidates": len(selected),
        }
        logger.info("Cycle stage diagnostics=%s", stage_diagnostics)
        _write_cycle_report(
            config=config,
            summary=summary,
            stage_diagnostics=stage_diagnostics,
            leader_diagnostics=leader_result.diagnostics,
        )
        return summary
    finally:
        state.close()


def calibrate_dry_runs(config: BotConfig, state: StateStore, data_client: ResilientDataClient) -> None:
    """
    Tracks subsequent price movement for dry_run trades to verify profitability (Issue #14).
    """
    active = state.get_active_simulated_performance()
    if not active:
        return

    # Group tokens by trade_id
    trades: dict[str, dict] = {}
    for row in active:
        tid = row["trade_id"]
        if tid not in trades:
            trades[tid] = {"tokens": []}
        trades[tid]["tokens"].append(row["token_id"])

    # Collect unique tokens to fetch prices efficiently
    all_tokens = list({t for tdata in trades.values() for t in tdata["tokens"]})

    prices: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=min(config.max_workers, len(all_tokens) or 1)) as executor:
        results = list(executor.map(data_client.fetch_best_ask, all_tokens))
        for quote in results:
            if quote.best_ask is not None:
                prices[quote.token_id] = quote.best_ask

    for tid, tdata in trades.items():
        current_sum = 0.0
        valid = True
        for token in tdata["tokens"]:
            p = prices.get(token)
            if p is None:
                valid = False
                break
            current_sum += p

        if valid:
            state.update_simulated_mark(tid, current_sum)


def run_bot_loop(config: BotConfig) -> None:
    logger.info(
        "Starting polymarket bot mode=%s min_group=%s max_group=%s sports_only=%s "
        "no_basket=%s pair=%s multi=%s scan_limit=%s min_net_profit=%s min_net_edge=%s aggression=%s",
        config.mode,
        config.min_group_size,
        config.max_group_size,
        config.sports_only,
        config.enable_no_basket_strategy,
        config.enable_binary_pair_strategy,
        config.enable_multi_outcome_strategy,
        config.scan_limit,
        config.min_net_profit_usd,
        config.min_net_edge,
        config.aggression,
    )
    tuner = AutoTuner(config)
    current_config = config
    last_trade_at: dict[str, float] = {}
    while True:
        started = time.time()
        try:
            summary = run_bot_once(current_config, last_trade_at=last_trade_at)
        except Exception as e:
            # Critical Safety Guard (Issue #3): prevent the loop from terminating on transient errors.
            logger.error("Cycle failed with unhandled exception: %s", e, exc_info=True)
            # Bounded backoff before retrying to avoid hammering a failing endpoint.
            time.sleep(current_config.poll_interval_seconds * 2)
            continue

        decision = tuner.tune(current_config, summary)
        if decision.changed:
            logger.info(
                "Auto-tune applied reason=%s aggression=%.4f min_net_profit=%.4f min_net_edge=%.6f",
                decision.reason,
                decision.new_config.aggression,
                decision.new_config.min_net_profit_usd,
                decision.new_config.min_net_edge,
            )
            current_config = decision.new_config
        logger.info(
            "Cycle summary scanned=%s groups=%s selected=%s executed=%s near_misses=%s diagnostics=%s",
            summary.scanned_markets,
            summary.eligible_groups,
            len(summary.opportunities),
            len(summary.executions),
            len(summary.near_misses),
            summary.diagnostics,
        )
        elapsed = time.time() - started
        # Bug 5 fix: use current_config (which may have been updated by auto-tuner), not the original config.
        time.sleep(max(0.05, current_config.poll_interval_seconds - elapsed))
