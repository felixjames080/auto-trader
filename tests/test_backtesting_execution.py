"""Focused tests for backtesting exit behavior.

Run from the backend directory after placing backtesting_corrected.py at
app/services/backtesting.py:
    python -m pytest tests/test_backtesting_execution.py -q
"""
from types import SimpleNamespace

from app.services.backtesting import _determine_exit


def candle(open_price, high, low):
    return SimpleNamespace(open=open_price, high=high, low=low)


def test_buy_stop_gap_fills_at_open():
    price, reason = _determine_exit(
        "BUY", candle(open_price=1.0930, high=1.0960, low=1.0920),
        stop_loss=1.0950, take_profit=1.1020,
    )
    assert price == 1.0930
    assert reason == "STOP_LOSS_GAP"


def test_sell_stop_gap_fills_at_open():
    price, reason = _determine_exit(
        "SELL", candle(open_price=1.1070, high=1.1080, low=1.1040),
        stop_loss=1.1050, take_profit=1.0980,
    )
    assert price == 1.1070
    assert reason == "STOP_LOSS_GAP"


def test_both_intrabar_levels_touched_stop_first():
    price, reason = _determine_exit(
        "BUY", candle(open_price=1.1000, high=1.1030, low=1.0940),
        stop_loss=1.0950, take_profit=1.1020,
    )
    assert price == 1.0950
    assert reason == "STOP_LOSS"


def test_buy_take_profit_gap_uses_target_price():
    price, reason = _determine_exit(
        "BUY", candle(open_price=1.1030, high=1.1040, low=1.1025),
        stop_loss=1.0950, take_profit=1.1020,
    )
    assert price == 1.1020
    assert reason == "TAKE_PROFIT"
