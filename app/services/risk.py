from dataclasses import dataclass

from app.services.position_sizing import InstrumentSpec


@dataclass
class RiskSettings:
    account_equity: float
    max_position_size: float
    max_open_trades: int
    current_open_trades: int
    max_risk_per_trade_percent: float
    current_daily_loss_percent: float
    max_daily_loss_percent: float
    trading_paused: bool = False


@dataclass
class ProposedTrade:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float


@dataclass
class RiskResult:
    approved: bool
    reasons: list[str]
    estimated_risk_amount: float
    estimated_risk_percent: float
    reward_risk_ratio: float


def validate_trade(
    trade: ProposedTrade,
    settings: RiskSettings,
    instrument: InstrumentSpec | None = None,
) -> RiskResult:

    reasons = []

    # -------------------------------------------------
    # Basic validation
    # -------------------------------------------------

    if trade.quantity <= 0:
        reasons.append("Position size must be greater than zero.")

    if trade.entry_price <= 0:
        reasons.append("Entry price must be greater than zero.")

    if trade.stop_loss <= 0:
        reasons.append("Stop-loss must be greater than zero.")

    if trade.take_profit <= 0:
        reasons.append("Take-profit must be greater than zero.")

    if settings.account_equity <= 0:
        reasons.append("Account equity must be greater than zero.")

    if settings.trading_paused:
        reasons.append("Trading is currently paused.")

    # -------------------------------------------------
    # Position limits
    # -------------------------------------------------

    if trade.quantity > settings.max_position_size:
        reasons.append(
            f"Position size {trade.quantity} exceeds "
            f"maximum allowed size {settings.max_position_size}."
        )

    if settings.current_open_trades >= settings.max_open_trades:
        reasons.append(
            "Maximum number of open trades has been reached."
        )

    # -------------------------------------------------
    # Direction validation
    # -------------------------------------------------

    side = trade.side.upper()

    if side == "BUY":

        if trade.stop_loss >= trade.entry_price:
            reasons.append(
                "For BUY trades, stop-loss must be below entry price."
            )

        if trade.take_profit <= trade.entry_price:
            reasons.append(
                "For BUY trades, take-profit must be above entry price."
            )

    elif side == "SELL":

        if trade.stop_loss <= trade.entry_price:
            reasons.append(
                "For SELL trades, stop-loss must be above entry price."
            )

        if trade.take_profit >= trade.entry_price:
            reasons.append(
                "For SELL trades, take-profit must be below entry price."
            )

    else:
        reasons.append("Trade side must be BUY or SELL.")

    # -------------------------------------------------
    # Risk / reward distances
    # -------------------------------------------------

    risk_distance = abs(
        trade.entry_price - trade.stop_loss
    )

    reward_distance = abs(
        trade.take_profit - trade.entry_price
    )

    # -------------------------------------------------
    # Monetary risk
    #
    # IMPORTANT:
    # When instrument specifications are available,
    # calculate risk using tick_size and tick_value.
    #
    # This matches position_sizing.py.
    # -------------------------------------------------

    estimated_risk_amount = 0.0

    if instrument is not None:

        if instrument.tick_size <= 0:
            reasons.append(
                "Instrument tick size must be greater than zero."
            )

        if instrument.tick_value <= 0:
            reasons.append(
                "Instrument tick value must be greater than zero."
            )

        if risk_distance > 0 and instrument.tick_size > 0:

            risk_ticks = (
                risk_distance / instrument.tick_size
            )

            risk_per_unit = (
                risk_ticks * instrument.tick_value
            )

            estimated_risk_amount = (
                risk_per_unit * trade.quantity
            )

    else:
        # Development fallback only.
        #
        # Live trading must always provide real
        # broker/platform instrument specifications.
        estimated_risk_amount = (
            risk_distance * trade.quantity
        )

    # -------------------------------------------------
    # Risk percentage
    # -------------------------------------------------

    if settings.account_equity > 0:

        estimated_risk_percent = (
            estimated_risk_amount
            / settings.account_equity
        ) * 100

    else:
        estimated_risk_percent = 0.0

    # -------------------------------------------------
    # Maximum risk check
    # -------------------------------------------------

    if (
        estimated_risk_percent
        > settings.max_risk_per_trade_percent
    ):
        reasons.append(
            f"Estimated risk of "
            f"{estimated_risk_percent:.2f}% exceeds "
            f"the configured maximum of "
            f"{settings.max_risk_per_trade_percent:.2f}%."
        )

    # -------------------------------------------------
    # Daily loss protection
    # -------------------------------------------------

    if (
        settings.current_daily_loss_percent
        >= settings.max_daily_loss_percent
    ):
        reasons.append(
            "Maximum daily loss has already been reached."
        )

    # -------------------------------------------------
    # Reward / risk
    # -------------------------------------------------

    if risk_distance > 0:

        reward_risk_ratio = (
            reward_distance / risk_distance
        )

    else:

        reward_risk_ratio = 0.0

    if reward_risk_ratio <= 0:

        reasons.append(
            "Reward/risk ratio is invalid."
        )

    # -------------------------------------------------
    # Final decision
    # -------------------------------------------------

    approved = len(reasons) == 0

    if approved:

        reasons.append(
            "All configured risk checks passed."
        )

    return RiskResult(
        approved=approved,
        reasons=reasons,
        estimated_risk_amount=estimated_risk_amount,
        estimated_risk_percent=estimated_risk_percent,
        reward_risk_ratio=reward_risk_ratio,
    )