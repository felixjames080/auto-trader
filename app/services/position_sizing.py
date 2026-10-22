from dataclasses import dataclass
from math import floor


@dataclass
class InstrumentSpec:
    symbol: str

    # How price changes are measured
    tick_size: float

    # Monetary value of one tick for one unit
    tick_value: float

    # Broker-style volume constraints
    volume_min: float
    volume_max: float
    volume_step: float


@dataclass
class PositionSizeResult:
    quantity: float
    risk_amount: float
    risk_percent: float
    approved: bool
    reason: str


def round_volume_down(quantity: float, step: float) -> float:
    if step <= 0:
        raise ValueError("Volume step must be greater than zero.")

    return floor(quantity / step) * step


def calculate_position_size(
    account_equity: float,
    entry_price: float,
    stop_loss: float,
    max_risk_percent: float = 1.0,
    instrument: InstrumentSpec | None = None,
    max_position_size: float | None = None,
) -> PositionSizeResult:

    if account_equity <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Account equity must be greater than zero.",
        )

    if entry_price <= 0 or stop_loss <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Entry price and stop-loss must be greater than zero.",
        )

    if max_risk_percent <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Maximum risk percentage must be greater than zero.",
        )

    if instrument is None:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Instrument specification is required.",
        )

    if instrument.tick_size <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Instrument tick size must be greater than zero.",
        )

    if instrument.tick_value <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Instrument tick value must be greater than zero.",
        )

    if instrument.volume_min <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Instrument minimum volume must be greater than zero.",
        )

    if instrument.volume_max < instrument.volume_min:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Instrument volume limits are invalid.",
        )

    risk_distance = abs(entry_price - stop_loss)

    if risk_distance <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Entry and stop-loss cannot be the same price.",
        )

    risk_ticks = risk_distance / instrument.tick_size

    risk_per_unit = risk_ticks * instrument.tick_value

    if risk_per_unit <= 0:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason="Calculated monetary risk per unit is invalid.",
        )

    maximum_risk_amount = (
        account_equity * max_risk_percent / 100
    )

    raw_quantity = maximum_risk_amount / risk_per_unit

    quantity = min(
        raw_quantity,
        instrument.volume_max,
    )

    if max_position_size is not None:
        quantity = min(
            quantity,
            max_position_size,
        )

    quantity = round_volume_down(
        quantity,
        instrument.volume_step,
    )

    if quantity < instrument.volume_min:
        return PositionSizeResult(
            quantity=0,
            risk_amount=0,
            risk_percent=0,
            approved=False,
            reason=(
                "Calculated position size is below the "
                "instrument minimum volume."
            ),
        )

    risk_amount = risk_per_unit * quantity

    risk_percent = (
        risk_amount / account_equity
    ) * 100

    return PositionSizeResult(
        quantity=quantity,
        risk_amount=risk_amount,
        risk_percent=risk_percent,
        approved=True,
        reason=(
            "Position size calculated using instrument tick "
            "value, tick size, volume limits, and configured risk."
        ),
    )