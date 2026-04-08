"""
Entry point for improved event trader.

Uses enhanced scanner and position manager.
"""

import sys
from pathlib import Path
from datetime import datetime

# Add parent to path so we can import from event_trader
sys.path.insert(0, str(Path(__file__).parent.parent))

from event_trader_improved.config import EVConfig
from event_trader_improved.runtime import run_loop
from event_trader.logging_utils import configure_logging  # Changed from logging_config

def _create_run_dir() -> Path:
    """Create timestamped run directory."""
    base = Path("ev_runs_improved")
    base.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base / timestamp
    run_dir.mkdir(exist_ok=True)
    return run_dir

def main():
    """Run improved event trader."""
    config = EVConfig.from_env()
    run_dir = _create_run_dir()
    
    log_file = str(run_dir / "runtime.log")
    report_file = str(run_dir / "cycle_report.jsonl")
    db_file = str(run_dir / "state.sqlite3")
    
    configure_logging(log_file, config.log_json)
    
    print("=" * 60)
    print("EVENT TRADER - IMPROVED VERSION")
    print("=" * 60)
    print(f"Mode: {config.mode}")
    print(f"Kelly sizing: {config.use_kelly_sizing}")
    print(f"Min edge: {config.min_edge:.1%}")
    print(f"Trailing activation: {config.trailing_activation_pct:.1%}")
    print("=" * 60)
    
    run_loop(config, db_path_override=db_file, report_path_override=report_file)

if __name__ == "__main__":
    main()




