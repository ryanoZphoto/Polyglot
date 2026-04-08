# Testing the Improvements

## What Changed

### 1. Scanner (scanner.py)
- **Before**: Only checked if price < max_entry_price
- **After**: Calculates edge, filters by spread quality and volume
- **Impact**: Fewer but higher-quality signals

### 2. Position Sizing (scanner.py)
- **Before**: Fixed $2/$5/$8 per tier
- **After**: Kelly Criterion based on edge and odds
- **Impact**: Larger positions on high-edge opportunities, smaller on marginal ones

### 3. Exits (positions.py)
- **Before**: Trailing stop only after hitting full take-profit
- **After**: Trailing activates at 5% profit, tightens near event close
- **Impact**: Better profit protection, fewer "gave it all back" scenarios

## Running Side-by-Side

```bash
# Terminal 1: Original
python -m event_trader.main

# Terminal 2: Improved
python -m event_trader_improved.main
```

Both will run simultaneously and create separate databases/logs.

## What to Compare

### Metrics to Track

1. **Signal Quality**
   - Original: Count of signals generated
   - Improved: Should be fewer but with higher edge
   
2. **Position Sizing**
   - Original: Fixed amounts
   - Improved: Variable based on Kelly
   
3. **Exit Performance**
   - Original: How many hit TP vs SL?
   - Improved: How many caught by early trailing stop?

4. **Overall P&L**
   - Compare after 24-48 hours of dry-run
   - Improved should have better risk-adjusted returns

### Log Differences

Look for these in improved version logs:

```
insufficient_edge: X  # Signals skipped due to low edge
spread_too_wide: Y    # Illiquid markets filtered
Kelly sizing: $Z      # Variable position sizes
Trailing stop triggered: early activation  # Exits before full TP
Time decay: tightening stop  # Near event close
```

## Expected Results

- **Fewer entries** (higher quality filter)
- **Variable position sizes** (Kelly vs fixed)
- **More trailing stop exits** (activates earlier)
- **Better Sharpe ratio** (less drawdown, similar returns)

## Reverting if Needed

Just stop the improved version and continue with original. No changes to original files.