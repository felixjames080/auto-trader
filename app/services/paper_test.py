from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.trading import AuditEvent, PaperOrder, PaperPosition
from app.services.instruments import get_instrument_spec
from app.services.position_sizing import calculate_position_size
from app.services.risk import ProposedTrade, RiskSettings, validate_trade
from app.services.execution_costs import apply_entry_cost

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

def open_controlled_paper_test(
    db: Session,
    symbol: str = "EURUSD",
    account_equity: float = 10000.0,
    max_risk_percent: float = 1.0,
):
    """
    Explicit PAPER-ONLY lifecycle test.

    This does NOT use the autonomous strategy.
    It creates a deterministic test trade so that the complete
    paper execution lifecycle can be verified safely.
    """

    symbol = symbol.upper()

    # ---------------------------------------------------------
    # 1. Make sure this is a supported development instrument
    # ---------------------------------------------------------

    instrument = get_instrument_spec(symbol)

    if instrument is None:
        raise ValueError(
            f"No development instrument specification exists for {symbol}."
        )

    # ---------------------------------------------------------
    # 2. Prevent duplicate open test positions
    # ---------------------------------------------------------

    existing = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.symbol == symbol,
            PaperPosition.status == "open",
        )
        .first()
    )

    if existing:
        raise ValueError(
            f"{symbol} already has an open paper position: "
            f"{existing.position_id}"
        )

    # ---------------------------------------------------------
    # 3. Deterministic test market setup
    # ---------------------------------------------------------

    side = "BUY"

    entry_price = 1.10000

    # These are deliberately explicit test values.
    # The autonomous strategy remains market-derived.
    stop_loss = 1.09900
    take_profit = 1.10200

    # ---------------------------------------------------------
    # 4. Calculate position size using instrument specification
    # ---------------------------------------------------------

    sizing = calculate_position_size(
        account_equity=account_equity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        max_risk_percent=max_risk_percent,
        instrument=instrument,
        max_position_size=instrument.volume_max,
    )

    if not sizing.approved:
        raise ValueError(
            f"Test position sizing failed: {sizing.reason}"
        )

    # ---------------------------------------------------------
    # 5. Build proposed trade
    # ---------------------------------------------------------

    trade = ProposedTrade(
        symbol=symbol,
        side=side,
        quantity=sizing.quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    # ---------------------------------------------------------
    # 6. Run normal risk validation
    # ---------------------------------------------------------

    risk_settings = RiskSettings(
        account_equity=account_equity,
        max_position_size=instrument.volume_max,
        max_open_trades=1,
        current_open_trades=0,
        max_risk_per_trade_percent=max_risk_percent,
        current_daily_loss_percent=0.0,
        max_daily_loss_percent=5.0,
        trading_paused=False,
    )

    risk_result = validate_trade(
    trade=trade,
    settings=risk_settings,
    instrument=instrument,
        )

    if not risk_result.approved:
        raise ValueError(
            "Controlled paper test was rejected by risk controls: "
            + " | ".join(risk_result.reasons)
        )

    # ---------------------------------------------------------
    # 7. Apply development execution cost
    # ---------------------------------------------------------

    # DEVELOPMENT ONLY.
    paper_spread = 0.00010
    paper_entry_slippage = 0.00002

    actual_entry_price = apply_entry_cost(
        side=side,
        requested_price=entry_price,
        spread=paper_spread,
        slippage=paper_entry_slippage,
    )

    # ---------------------------------------------------------
    # 8. Generate IDs
    # ---------------------------------------------------------

    execution_id = f"paper-test-exec-{uuid4()}"
    position_id = f"paper-test-pos-{uuid4()}"

    # ---------------------------------------------------------
    # 9. Create paper order
    # ---------------------------------------------------------

    order = PaperOrder(
        execution_id=execution_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(str(sizing.quantity)),
        entry_price=Decimal(str(actual_entry_price)),
        stop_loss=Decimal(str(stop_loss)),
        take_profit=Decimal(str(take_profit)),
        status="filled",
    )

    # ---------------------------------------------------------
    # 10. Create paper position
    # ---------------------------------------------------------

    position = PaperPosition(
        position_id=position_id,
        execution_id=execution_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(str(sizing.quantity)),
        entry_price=Decimal(str(actual_entry_price)),
        current_price=Decimal(str(actual_entry_price)),
        stop_loss=Decimal(str(stop_loss)),
        take_profit=Decimal(str(take_profit)),
        status="open",
        realized_pnl=Decimal("0"),
    )

    # ---------------------------------------------------------
    # 11. Audit
    # ---------------------------------------------------------

    audit = AuditEvent(
        event_type="PAPER_TEST_TRADE_OPENED",
        outcome="SUCCESS",
        description=(
            f"Controlled PAPER lifecycle test opened. "
            f"Symbol={symbol}, "
            f"Side={side}, "
            f"RequestedEntry={entry_price}, "
            f"ActualEntry={actual_entry_price}, "
            f"SL={stop_loss}, "
            f"TP={take_profit}, "
            f"Quantity={sizing.quantity}, "
            f"RiskAmount={sizing.risk_amount}, "
            f"RiskPercent={sizing.risk_percent:.4f}%."
        ),
    )

    # ---------------------------------------------------------
    # 12. Persist atomically
    # ---------------------------------------------------------

    try:
        db.add(order)
        db.add(position)
        db.add(audit)

        db.commit()

        db.refresh(order)
        db.refresh(position)

    except Exception:
        db.rollback()
        raise

    return {
        "mode": "PAPER_TEST_ONLY",
        "status": "OPEN",
        "execution_id": execution_id,
        "position_id": position_id,
        "symbol": symbol,
        "side": side,
        "requested_entry_price": entry_price,
        "actual_entry_price": actual_entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": sizing.quantity,
        "risk_amount": sizing.risk_amount,
        "risk_percent": sizing.risk_percent,
        "reward_risk_ratio": risk_result.reward_risk_ratio,
        "message": (
            "Controlled paper test position opened successfully. "
            "No live order was sent."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }