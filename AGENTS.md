# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Polymarket Trading Bot Suite — four Python trading bots with Streamlit dashboards for the Polymarket prediction market platform. Pure Python, no Docker/containers, no external DB servers (uses embedded SQLite).

### Running tests

```bash
pytest
```

Pytest now bootstraps the workspace root automatically via the repo-level `conftest.py`, so a plain `pytest` invocation works from `/workspace`. `PYTHONPATH=/workspace` remains acceptable for other tooling, but it is no longer required just to run tests.

### Running bots (dry_run mode, no credentials needed)

```bash
PYTHONPATH=/workspace python3 -m polymarket_bot.main --once            # single cycle
PYTHONPATH=/workspace python3 -m polymarket_bot.main                   # continuous loop
PYTHONPATH=/workspace python3 -m event_trader.main --once             # single cycle
PYTHONPATH=/workspace python3 -m event_trader.main                    # continuous loop
PYTHONPATH=/workspace python3 -m event_trader_improved.main --once    # single cycle
PYTHONPATH=/workspace python3 -m event_trader_improved.main           # continuous loop
PYTHONPATH=/workspace python3 -m market_maker.main --once             # single cycle
PYTHONPATH=/workspace python3 -m market_maker.main                    # continuous loop
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

- **Prefer `PYTHONPATH=/workspace` for module commands**: `python -m ...` and Streamlit commands should still be run with `PYTHONPATH=/workspace` in cloud environments. Plain `pytest` now works without it because the test bootstrap injects the repo root during collection.
- **No linter configured**: There is no linting tool (flake8, ruff, pylint, mypy) in `requirements.txt` or project config. Tests are the primary quality gate.
- **`event_trader_improved` has no `__init__.py`**: It still works as a runnable package via `__main__.py` conventions, but direct imports from other code may fail.
- **Bots need internet**: Even in dry_run mode, bots call `gamma-api.polymarket.com` and `clob.polymarket.com`.
- **Live trading requires secrets**: `PM_PRIVATE_KEY`, `PM_API_KEY`, `PM_API_SECRET`, `PM_API_PASSPHRASE` (see README for full list).
