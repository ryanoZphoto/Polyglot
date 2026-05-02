# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Polymarket Trading Bot Suite — four Python trading bots with Streamlit dashboards for the Polymarket prediction market platform. Pure Python, no Docker/containers, no external DB servers (uses embedded SQLite).

### Running tests

```bash
PYTHONPATH=/workspace pytest
```

The `PYTHONPATH=/workspace` is **required** because there is no `setup.py` / `pyproject.toml`; the project's packages (`polymarket_bot`, `event_trader`, `event_trader_improved`, `market_maker`) must be importable from the workspace root.

### Running bots (dry_run mode, no credentials needed)

```bash
PYTHONPATH=/workspace python3 -m polymarket_bot.main --once   # single cycle
PYTHONPATH=/workspace python3 -m polymarket_bot.main          # continuous loop
PYTHONPATH=/workspace python3 -m event_trader.main            # continuous loop (no --once)
PYTHONPATH=/workspace python3 -m event_trader_improved.main   # continuous loop
PYTHONPATH=/workspace python3 -m market_maker.main            # continuous loop
```

All bots default to `dry_run` mode — they scan live Polymarket APIs but do not place real orders. Internet access is required for all bots.

### Running dashboards (Streamlit)

```bash
PYTHONPATH=/workspace streamlit run bot_ui.py --server.headless true --server.port 8501
PYTHONPATH=/workspace streamlit run ev_ui.py --server.headless true --server.port 8502
PYTHONPATH=/workspace streamlit run ev_improved_ui.py --server.headless true --server.port 8503
PYTHONPATH=/workspace streamlit run mm_ui.py --server.headless true --server.port 8504
```

### Key gotchas

- **PYTHONPATH is always needed**: Every `python -m` or `pytest` invocation needs `PYTHONPATH=/workspace` since the project lacks a proper packaging setup.
- **No linter configured**: There is no linting tool (flake8, ruff, pylint, mypy) in `requirements.txt` or project config. Tests are the primary quality gate.
- **`event_trader_improved` has no `__init__.py`**: It still works as a runnable package via `__main__.py` conventions, but direct imports from other code may fail.
- **Bots need internet**: Even in dry_run mode, bots call `gamma-api.polymarket.com` and `clob.polymarket.com`.
- **Live trading requires secrets**: `PM_PRIVATE_KEY`, `PM_API_KEY`, `PM_API_SECRET`, `PM_API_PASSPHRASE` (see README for full list).
