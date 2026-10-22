from datetime import datetime, timezone
from decimal import Decimal
from app.services.instruments import get_instrument_spec
from sqlalchemy.orm import Session

from app.models.trading import AuditEvent, PaperPosition
from app.services.execution_costs import (
    apply_exit_cost,
    calculate_net_pnl,
)
from app.services.paper_trading import check_protection_trigger


# ---------------------------------------------------------
# DEVELOPMENT PAPER-TRADING COST SETTINGS
# ---------------------------------------------------------

# These are ONLY simulation values.
# Real broker values must come from the connected platform.

PAPER_SPREAD = 0.00010
PAPER_EXIT_SLIPPAGE = 0.00002
PAPER_COMMISSION_PER_UNIT = 0.0


def monitor_paper_positions(
    db: Session,
    symbol: str,
    current_price: float,
) -> list[dict]:

    symbol = symbol.upper()

    current_price_decimal = Decimal(
        str(current_price)
    )

    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.symbol == symbol,
            PaperPosition.status == "open",
        )
        .all()
    )

    results = []

    for position in positions:

        position.current_price = current_price_decimal

        trigger = check_protection_trigger(
            side=position.side,
            current_price=current_price,
            stop_loss=float(position.stop_loss),
            take_profit=float(position.take_profit),
        )

        # -------------------------------------------------
        # Position remains open
        # -------------------------------------------------

        if trigger is None:

            results.append(
                {
                    "position_id": position.position_id,
                    "symbol": position.symbol,
                    "status": "OPEN",
                    "current_price": current_price,
                    "realized_pnl": None,
                    "gross_pnl": None,
                    "execution_cost": None,
                    "close_reason": None,
                }
            )

            continue

        # -------------------------------------------------
        # Position is closing
        # -------------------------------------------------

        entry_price = float(position.entry_price)
        quantity = float(position.quantity)

        # Simulate the actual exit price after spread/slippage.
        actual_exit_price = apply_exit_cost(
            side=position.side,
            requested_price=current_price,
            spread=PAPER_SPREAD,
            slippage=PAPER_EXIT_SLIPPAGE,
        )

        # -------------------------------------------------
        # Get instrument specification
        # -------------------------------------------------

        instrument = get_instrument_spec(position.symbol)

        if instrument is None:
            raise ValueError(
                f"No instrument specification exists for {position.symbol}."
            )

        # -------------------------------------------------
        # Calculate monetary P&L using tick specifications
        # -------------------------------------------------

        gross_pnl, net_pnl = calculate_net_pnl(
            side=position.side,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=actual_exit_price,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
            total_execution_cost=(
                quantity * abs(PAPER_COMMISSION_PER_UNIT)
            ),
        )

        # -------------------------------------------------
        # Close position
        # -------------------------------------------------

        position.status = "closed"

        position.closed_at = datetime.now(timezone.utc)

        position.close_reason = trigger

        position.realized_pnl = Decimal(
            str(round(net_pnl, 8))
        )

        # -------------------------------------------------
        # Audit event
        # -------------------------------------------------

        audit = AuditEvent(
            event_type="PAPER_POSITION_CLOSED",
            outcome="SUCCESS",
            description=(
                f"Paper position {position.position_id} "
                f"closed on {trigger}. "
                f"Symbol={position.symbol}, "
                f"Side={position.side}, "
                f"Entry={entry_price}, "
                f"RequestedExit={current_price}, "
                f"ActualExit={actual_exit_price}, "
                f"Quantity={quantity}, "
                f"GrossPnL={gross_pnl}, "
                f"NetPnL={net_pnl}, "
                f"ExitSpread={PAPER_SPREAD}, "
                f"ExitSlippage={PAPER_EXIT_SLIPPAGE}, "
                f"Commission="
                f"{quantity * abs(PAPER_COMMISSION_PER_UNIT)}"
            ),
        )

        db.add(audit)

        results.append(
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "status": "CLOSED",
                "current_price": current_price,
                "actual_exit_price": actual_exit_price,
                "gross_pnl": round(gross_pnl, 8),
                "realized_pnl": round(net_pnl, 8),
                "close_reason": trigger,
            }
        )

    db.commit()

    return results