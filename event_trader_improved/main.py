"""Entry point for improved event trader."""

import sys
from pathlib import Path
from datetime import datetime

# Add parent to path so we can import from event_trader
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import logging
from datetime import datetime
from dataclasses import asdict, replace
from pathlib import Path

# Add parent to path so we can import from event_trader
sys.path.insert(0, str(Path(__file__).parent.parent))

from event_trader_improved.config import ImprovedEVConfig
from event_trader_improved.runtime import run_loop


def _setup_logging(run_dir: Path, log_json: bool):
    """Configure logging to both console and file."""
    log_file = run_dir / "runtime.log"
    
    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    
    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(console)
    
    # File handler
    file_h = logging.FileHandler(log_file)
    file_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(file_h)


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
    config = ImprovedEVConfig.from_env()
    run_dir = _create_run_dir()
    
    # Setup paths in config
    config = replace(
        config,
        state_db_path=str(run_dir / "state.sqlite3"),
        log_path=str(run_dir / "runtime.log"),
        report_path=str(run_dir / "cycle_report.jsonl")
    )
    
    # Setup logging
    _setup_logging(run_dir, config.log_json)
    
    # Export config for UI
    with open(run_dir / "config.json", "w") as f:
        json.dump(asdict(config), f, indent=2)
    
    print("=" * 60)
    print("EVENT TRADER - IMPROVED VERSION")
    print("=" * 60)
    print(f"Mode: {config.mode}")
    print(f"Kelly sizing: {config.use_kelly_sizing}")
    print(f"Min edge: {config.min_edge:.1%}")
    print(f"Run directory: {run_dir}")
    print("=" * 60)
    
    run_loop(config)

if __name__ == "__main__":
    main()

