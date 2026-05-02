from __future__ import annotations

from event_trader_improved.main import _parse_args


def test_parse_args_once_flag() -> None:
    args = _parse_args(["--once"])
    assert args.once is True
