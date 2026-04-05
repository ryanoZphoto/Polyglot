from __future__ import annotations

import json
import logging

import requests

from .config import BotConfig
from .types import Opportunity

logger = logging.getLogger(__name__)


class OpportunityRanker:
    def __init__(self, config: BotConfig):
        self.config = config

    def rank(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        if not self.config.enable_llm_ranking:
            return opportunities
        if not self.config.llm_api_key:
            logger.warning("LLM ranking enabled but BOT_LLM_API_KEY missing; skipping LLM ranking")
            return opportunities
        if not opportunities:
            return opportunities

        # LLM can only rank/filter scanner candidates; it never invents new trades.
        prompt_rows = []
        for idx, opp in enumerate(opportunities):
            prompt_rows.append(
                {
                    "idx": idx,
                    "group_key": opp.group_key,
                    "edge": opp.edge,
                    "expected_profit_usd": opp.expected_profit_usd,
                    "sum_ask": opp.sum_ask,
                    "legs": len(opp.legs),
                }
            )
        body = {
            "model": self.config.llm_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative trading assistant. Rank opportunities by execution quality. "
                        "Return strict JSON: {\"keep_indices\": [int,...], \"reason\": \"...\"}. "
                        "Never include indices outside input range."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_rows)},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                self.config.llm_endpoint,
                headers=headers,
                json=body,
                timeout=self.config.request_timeout_seconds,
            )
            resp.raise_for_status()
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            indices = parsed.get("keep_indices", [])
            if not isinstance(indices, list):
                return opportunities
            kept = [opportunities[i] for i in indices if isinstance(i, int) and 0 <= i < len(opportunities)]
            if kept:
                logger.info("LLM ranked opportunities kept=%s total=%s", len(kept), len(opportunities))
                return kept
            return opportunities
        except Exception as exc:
            logger.warning("LLM ranking failed, using deterministic ordering: %s", exc)
            return opportunities
