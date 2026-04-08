# Event Trader - Improved Version

This folder contains enhanced versions of the core trading logic with:

1. **Edge-based signals** - Calculate expected value, not just price tiers
2. **Volatility-adjusted sizing** - Kelly Criterion instead of fixed amounts
3. **Better exit logic** - Trailing stops activate immediately on any profit

## Changes from Original

### scanner.py
- Added edge calculation (your probability vs market price)
- Added market quality filters (spread, depth, volume)
- Signals now include confidence scores

### positions.py
- Trailing stop activates on ANY profit (not just after TP)
- Added time-decay logic for events approaching close
- Volatility-based stop adjustments

### config.py
- Added edge calculation parameters
- Added volatility adjustment factors
- Kept backward compatibility with original params

### runtime.py
- Minimal changes - just uses new signal/position logic

## Testing

```bash
# Run improved version
python -m event_trader_improved.main

# Compare with original
python -m event_trader.main
```

Both will create separate run folders so you can compare results.