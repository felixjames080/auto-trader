from dataclasses import dataclass

from app.services.market_data import Candle


@dataclass
class StrategySignal:
    direction: str
    reason: list[str]
    indicators: dict
    candle_count: int


def calculate_ema(values: list[float], period: int) -> list[float]:
    """
    Calculate Exponential Moving Average.
    """

    if len(values) < period:
        raise ValueError(
            f"At least {period} values are required for EMA."
        )

    multiplier = 2 / (period + 1)

    ema = [sum(values[:period]) / period]

    for price in values[period:]:
        next_ema = (
            (price - ema[-1]) * multiplier
            + ema[-1]
        )

        ema.append(next_ema)

    # Pad the beginning so the returned list has the
    # same length as the input.
    padding = [None] * (period - 1)

    return padding + ema


def calculate_rsi(
    values: list[float],
    period: int = 14,
) -> list[float]:
    """
    Calculate standard RSI using average gains/losses.
    """

    if len(values) <= period:
        raise ValueError(
            f"At least {period + 1} values are required for RSI."
        )

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [
        max(change, 0)
        for change in changes
    ]

    losses = [
        max(-change, 0)
        for change in changes
    ]

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    rsi_values = [None] * period

    if average_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = average_gain / average_loss
        rsi_values.append(
            100 - (100 / (1 + rs))
        )

    for i in range(period, len(changes)):
        average_gain = (
            (average_gain * (period - 1))
            + gains[i]
        ) / period

        average_loss = (
            (average_loss * (period - 1))
            + losses[i]
        ) / period

        if average_loss == 0:
            rsi = 100.0
        else:
            rs = average_gain / average_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_values.append(rsi)

    return rsi_values


def generate_signal(
    candles: list[Candle],
    fast_ema_period: int = 20,
    slow_ema_period: int = 50,
    rsi_period: int = 14,
    rsi_buy_threshold: float = 55.0,
    rsi_sell_threshold: float = 45.0,
) -> StrategySignal:

    minimum_required = max(
        slow_ema_period,
        rsi_period + 1,
    )

    if len(candles) < minimum_required:
        raise ValueError(
            f"Not enough candles. Need at least "
            f"{minimum_required}."
        )

    closes = [
        candle.close
        for candle in candles
    ]

    fast_ema = calculate_ema(
        closes,
        fast_ema_period,
    )

    slow_ema = calculate_ema(
        closes,
        slow_ema_period,
    )

    rsi = calculate_rsi(
        closes,
        rsi_period,
    )

    current_fast_ema = fast_ema[-1]
    current_slow_ema = slow_ema[-1]
    current_rsi = rsi[-1]
    current_price = closes[-1]

    reasons: list[str] = []

    # Trend conditions.
    bullish_trend = (
        current_fast_ema is not None
        and current_slow_ema is not None
        and current_fast_ema > current_slow_ema
    )

    bearish_trend = (
        current_fast_ema is not None
        and current_slow_ema is not None
        and current_fast_ema < current_slow_ema
    )

    # Momentum conditions.
    bullish_momentum = (
        current_rsi is not None
        and current_rsi >= rsi_buy_threshold
    )

    bearish_momentum = (
        current_rsi is not None
        and current_rsi <= rsi_sell_threshold
    )

    if bullish_trend:
        reasons.append(
            "Fast EMA is above slow EMA."
        )

    if bearish_trend:
        reasons.append(
            "Fast EMA is below slow EMA."
        )

    if bullish_momentum:
        reasons.append(
            f"RSI is at or above "
            f"{rsi_buy_threshold:.1f}."
        )

    if bearish_momentum:
        reasons.append(
            f"RSI is at or below "
            f"{rsi_sell_threshold:.1f}."
        )

    if bullish_trend and bullish_momentum:
        direction = "BUY"

    elif bearish_trend and bearish_momentum:
        direction = "SELL"

    else:
        direction = "NEUTRAL"

        if not reasons:
            reasons.append(
                "No qualifying trend or momentum condition."
            )
        else:
            reasons.append(
                "Trend and momentum conditions "
                "did not align for an entry."
            )

    return StrategySignal(
        direction=direction,
        reason=reasons,
        indicators={
            "price": current_price,
            "ema_fast": current_fast_ema,
            "ema_slow": current_slow_ema,
            "rsi": current_rsi,
        },
        candle_count=len(candles),
    )