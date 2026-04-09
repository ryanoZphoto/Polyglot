"""Improved scanner with better signal detection."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    market_id: str
    token_id: str
    outcome: str
    question: str
    price: float
    edge: float
    spread_pct: float
    volume_24h: float
    strategy: str = "SINGLE"
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    size_usd: float = 0.0


class Scanner:
    def __init__(self, config):
        self.config = config
        self.max_entry_price = config.max_entry_price
        self.min_edge = config.min_edge
        self.max_spread_pct = config.max_spread_pct
        self.min_volume_24h = config.min_volume_24h
    
    def scan(self, markets: list) -> list[Signal]:
        """
        Scan markets for the 'Primal Sweet Spot':
        1. Arbitrage (Zero-risk mathematically)
        2. Momentum (Value + Velocity)
        """
        signals = []
        eligible_markets = []
        skipped_active = 0
        skipped_volume = 0
        
        # 1. First Pass: Metadata Extraction & Basic Filtering
        for mkt in markets:
            if hasattr(mkt, "market_id"):
                is_active = getattr(mkt, "active", False)
                is_closed = getattr(mkt, "closed", False)
                if not is_active or is_closed:
                    skipped_active += 1
                    continue
                
                market_id = getattr(mkt, "market_id", "")
                question = getattr(mkt, "question", "")
                volume = float(getattr(mkt, "volume", 0))
                event_slug = getattr(mkt, "event_slug", "") or ""
                
                outcomes = getattr(mkt, "outcomes", [])
                token_ids = getattr(mkt, "token_ids", [])
                prices = getattr(mkt, "outcome_prices", [])
                
                tokens = []
                for i in range(len(token_ids)):
                    tokens.append({
                        "outcome": outcomes[i],
                        "token_id": token_ids[i],
                        "price": float(prices[i]) if i < len(prices) else 0.0
                    })
            elif isinstance(mkt, dict):
                if not mkt.get("active", False) or mkt.get("closed", False):
                    skipped_active += 1
                    continue
                
                market_id = mkt.get("id") or mkt.get("condition_id", "")
                question = mkt.get("question", "")
                volume = float(mkt.get("volume", 0))
                event_slug = mkt.get("event_slug", "")
                
                raw_tokens = mkt.get("tokens", [])
                tokens = []
                for t in raw_tokens:
                    tokens.append({
                        "outcome": t.get("outcome"),
                        "token_id": t.get("token_id"),
                        "price": float(t.get("price", 0))
                    })
            else:
                continue

            if volume < self.min_volume_24h:
                skipped_volume += 1
                continue

            eligible_markets.append({
                "market_id": market_id,
                "question": question,
                "volume": volume,
                "event_slug": event_slug,
                "tokens": tokens
            })

        # Group by Event Slug for NO_BASKET
        event_groups = {}
        for m in eligible_markets:
            if m["event_slug"]:
                event_groups.setdefault(m["event_slug"], []).append(m)

        for mkt in eligible_markets:
            market_id = mkt["market_id"]
            question = mkt["question"]
            volume = mkt["volume"]
            tokens = mkt["tokens"]
            num_outcomes = len(tokens)
            
            # --- Strategy 1: ARBITRAGE (The Ultimate Sweet Spot) ---
            # Sum of prices < 1.0 means guaranteed profit if held to resolution
            total_price = sum(t["price"] for t in tokens)
            if 0.1 < total_price < (1.0 - self.min_edge):
                edge = 1.0 - total_price
                strat = "BINARY_PAIR" if num_outcomes == 2 else "MULTI_OUTCOME"
                for t in tokens:
                    signals.append(Signal(
                        market_id=market_id,
                        token_id=t["token_id"],
                        outcome=t["outcome"],
                        question=question,
                        price=t["price"],
                        edge=edge,
                        spread_pct=0.005,
                        volume_24h=volume,
                        strategy=strat,
                        target_price=min(0.99, t["price"] / total_price), # Fair value in arb
                        stop_loss_price=t["price"] * 0.95 # Tight stop for arbs
                    ))
                continue

            # --- Strategy 2: MOMENTUM & VALUE ---
            if self.config.enable_single_outcome:
                for t in tokens:
                    price = t["price"]
                    if price <= 0.01 or price > self.max_entry_price:
                        continue
                    
                    # SWEET SPOT CALCULATION:
                    # We combine raw distance from 0.5 (Value) with a "Momentum Bonus"
                    momentum_bonus = 0.0
                    if volume > (self.min_volume_24h * 10):
                        momentum_bonus = 0.02
                    
                    edge = abs(0.5 - price) + momentum_bonus
                    if edge >= self.min_edge:
                        signals.append(Signal(
                            market_id=market_id,
                            token_id=t["token_id"],
                            outcome=t["outcome"],
                            question=question,
                            price=price,
                            edge=edge,
                            spread_pct=0.005,
                            volume_24h=volume,
                            strategy="SINGLE",
                            target_price=min(0.95, price * 1.15), # 15% target
                            stop_loss_price=price * 0.90 # 10% stop
                        ))

        # --- Strategy 3: CROSS-MARKET ARB (NO_BASKET) ---
        for slug, group in event_groups.items():
            if len(group) < 2: continue
            
            no_legs = []
            for m in group:
                for t in m["tokens"]:
                    if t["outcome"].lower() == "no" and 0 < t["price"] < 1:
                        no_legs.append({"m": m, "t": t})
                        break
            
            if len(no_legs) >= 2:
                total_no_price = sum(l["t"]["price"] for l in no_legs)
                n = len(no_legs)
                guaranteed_payout = float(n - 1)
                if 0.1 < total_no_price < (guaranteed_payout - self.min_edge):
                    edge = (guaranteed_payout - total_no_price) / n
                    for l in no_legs:
                        signals.append(Signal(
                            market_id=l["m"]["market_id"],
                            token_id=l["t"]["token_id"],
                            outcome=l["t"]["outcome"],
                            question=l["m"]["question"],
                            price=l["t"]["price"],
                            edge=edge,
                            spread_pct=0.005,
                            volume_24h=l["m"]["volume"],
                            strategy="NO_BASKET",
                            target_price=min(0.99, l["t"]["price"] * 1.1),
                            stop_loss_price=l["t"]["price"] * 0.98
                        ))

        logger.info(
            "Scan stats: scanned=%d signals=%d arbs=%d",
            len(markets), len(signals), 
            len([s for s in signals if "ARB" in s.strategy or "BASKET" in s.strategy])
        )
        
        # Sort by strategy priority then edge
        # 1. NO_BASKET/ARB first
        # 2. Then by Edge
        def signal_priority(s):
            priority = 0
            if "ARB" in s.strategy or "BASKET" in s.strategy:
                priority = 10
            return (priority, s.edge)

        signals.sort(key=signal_priority, reverse=True)
        return signals
