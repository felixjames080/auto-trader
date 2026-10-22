from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.services.risk import ProposedTrade, RiskSettings, validate_trade


@dataclass
class PaperExecutionResult:
    approved: bool
    execution_id: str
    position_id: str | None
    status: str
    reasons: list[str]


def execute_paper_trade(
    trade: ProposedTrade,
    risk_settings: RiskSettings,
) -> PaperExecutionResult:
    """
    Execute a paper trade after risk validation.

    This function NEVER sends an order to a broker.
    """

    risk_result = validate_trade(
        trade=trade,
        settings=risk_settings,
    )

    execution_id = f"paper-exec-{uuid4()}"

    if not risk_result.approved:
        return PaperExecutionResult(
            approved=False,
            execution_id=execution_id,
            position_id=None,
            status="rejected",
            reasons=risk_result.reasons,
        )

    position_id = f"paper-pos-{uuid4()}"

    return PaperExecutionResult(
        approved=True,
        execution_id=execution_id,
        position_id=position_id,
        status="open",
        reasons=risk_result.reasons,
    )


def calculate_paper_pnl(
    side: str,
    quantity: Decimal,
    entry_price: Decimal,
    current_price: Decimal,
) -> Decimal:
    """
    Basic paper-trading P/L calculation.

    BUY:
        profit when current price > entry price

    SELL:
        profit when current price < entry price
    """

    side = side.upper()

    if side == "BUY":
        return (current_price - entry_price) * quantity

    if side == "SELL":
        return (entry_price - current_price) * quantity

    raise ValueError("Side must be BUY or SELL.")


def check_protection_trigger(
    side: str,
    current_price: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> str | None:
    """
    Determine whether SL or TP has been triggered.
    """

    side = side.upper()

    if side == "BUY":
        if current_price <= stop_loss:
            return "STOP_LOSS"

        if current_price >= take_profit:
            return "TAKE_PROFIT"

    elif side == "SELL":
        if current_price >= stop_loss:
            return "STOP_LOSS"

        if current_price <= take_profit:
            return "TAKE_PROFIT"

    else:
        raise ValueError("Side must be BUY or SELL.")

    return None