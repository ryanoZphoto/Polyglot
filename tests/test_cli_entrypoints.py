from __future__ import annotations

import pytest

from event_trader import main as event_trader_main
from event_trader_improved import main as improved_main
from market_maker import main as market_maker_main


@pytest.mark.parametrize(
    ("module", "description"),
    [
        (event_trader_main, "Event-driven trader bot."),
        (improved_main, "Improved event-driven trader bot."),
        (market_maker_main, "Market-making bot."),
    ],
)
def test_help_exits_before_runtime(monkeypatch, capsys, module, description):
    monkeypatch.setattr("sys.argv", ["prog", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        module.parse_args()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert description in captured.out
    assert "--once" in captured.out


@pytest.mark.parametrize(
    ("module", "description"),
    [
        (event_trader_main, "Event-driven trader bot."),
        (improved_main, "Improved event-driven trader bot."),
        (market_maker_main, "Market-making bot."),
    ],
)
def test_once_flag_is_supported(monkeypatch, module, description):
    monkeypatch.setattr("sys.argv", ["prog", "--once"])
    args = module.parse_args()

    assert args.once is True
