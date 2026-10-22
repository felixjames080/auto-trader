from dataclasses import dataclass

from app.services.market_data import Candle
from app.strategies.ema_rsi import (
    calculate_ema,
    calculate_rsi,
)


@dataclass
class TradeDecision:
    action: str
    symbol: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_distance: float | None
    reward_distance: float | None
    reward_risk_ratio: float | None
    reasons: list[str]


def calculate_atr(
    candles: list[Candle],
    period: int = 14,
) -> float:
    """
    Calculate a basic Average True Range.

    ATR gives the bot a volatility measurement that can
    be used to place market-based protection levels.
    """

    if len(candles) <= period:
        raise ValueError(
            f"At least {period + 1} candles are required for ATR."
        )

    true_ranges: list[float] = []

    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]

        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )

        true_ranges.append(true_range)

    recent_ranges = true_ranges[-period:]

    return sum(recent_ranges) / period


def generate_trade_decision(
    candles: list[Candle],
    symbol: str,
    fast_ema_period: int = 20,
    slow_ema_period: int = 50,
    rsi_period: int = 14,
    rsi_buy_threshold: float = 55.0,
    rsi_sell_threshold: float = 45.0,
    atr_period: int = 14,
    stop_atr_multiplier: float = 1.5,
    target_atr_multiplier: float = 3.0,
    minimum_reward_risk: float = 1.5,
) -> TradeDecision:

    minimum_required = max(
        slow_ema_period,
        rsi_period + 1,
        atr_period + 1,
    )

    if len(candles) < minimum_required:
        raise ValueError(
            f"Not enough candles. Need at least "
            f"{minimum_required}."
        )

    closes = [candle.close for candle in candles]

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

    atr = calculate_atr(
        candles,
        atr_period,
    )

    entry_price = closes[-1]

    current_fast_ema = fast_ema[-1]
    current_slow_ema = slow_ema[-1]
    current_rsi = rsi[-1]

    reasons: list[str] = []

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

    bullish_momentum = (
        current_rsi is not None
        and current_rsi >= rsi_buy_threshold
    )

    bearish_momentum = (
        current_rsi is not None
        and current_rsi <= rsi_sell_threshold
    )

    # --------------------------------
    # BUY DECISION
    # --------------------------------

    if bullish_trend and bullish_momentum:

        stop_loss = (
            entry_price
            - (atr * stop_atr_multiplier)
        )

        take_profit = (
            entry_price
            + (atr * target_atr_multiplier)
        )

        risk_distance = (
            entry_price - stop_loss
        )

        reward_distance = (
            take_profit - entry_price
        )

        reward_risk_ratio = (
            reward_distance / risk_distance
            if risk_distance > 0
            else 0
        )

        reasons.append(
            "Fast EMA is above slow EMA."
        )

        reasons.append(
            f"RSI is {current_rsi:.2f}, "
            f"above the BUY threshold."
        )

        reasons.append(
            f"ATR is {atr:.6f}, providing a "
            "volatility-based protection distance."
        )

        if reward_risk_ratio < minimum_reward_risk:

            return TradeDecision(
                action="NO_TRADE",
                symbol=symbol,
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                risk_distance=None,
                reward_distance=None,
                reward_risk_ratio=reward_risk_ratio,
                reasons=[
                    *reasons,
                    (
                        f"Reward/risk ratio "
                        f"{reward_risk_ratio:.2f} is below "
                        f"the minimum required "
                        f"{minimum_reward_risk:.2f}."
                    ),
                ],
            )

        reasons.append(
            "BUY setup satisfies the minimum "
            "reward/risk requirement."
        )

        return TradeDecision(
            action="BUY",
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_distance=risk_distance,
            reward_distance=reward_distance,
            reward_risk_ratio=reward_risk_ratio,
            reasons=reasons,
        )

    # --------------------------------
    # SELL DECISION
    # --------------------------------

    if bearish_trend and bearish_momentum:

        stop_loss = (
            entry_price
            + (atr * stop_atr_multiplier)
        )

        take_profit = (
            entry_price
            - (atr * target_atr_multiplier)
        )

        risk_distance = (
            stop_loss - entry_price
        )

        reward_distance = (
            entry_price - take_profit
        )

        reward_risk_ratio = (
            reward_distance / risk_distance
            if risk_distance > 0
            else 0
        )

        reasons.append(
            "Fast EMA is below slow EMA."
        )

        reasons.append(
            f"RSI is {current_rsi:.2f}, "
            f"below the SELL threshold."
        )

        reasons.append(
            f"ATR is {atr:.6f}, providing a "
            "volatility-based protection distance."
        )

        if reward_risk_ratio < minimum_reward_risk:

            return TradeDecision(
                action="NO_TRADE",
                symbol=symbol,
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                risk_distance=None,
                reward_distance=None,
                reward_risk_ratio=reward_risk_ratio,
                reasons=[
                    *reasons,
                    (
                        f"Reward/risk ratio "
                        f"{reward_risk_ratio:.2f} is below "
                        f"the minimum required "
                        f"{minimum_reward_risk:.2f}."
                    ),
                ],
            )

        reasons.append(
            "SELL setup satisfies the minimum "
            "reward/risk requirement."
        )

        return TradeDecision(
            action="SELL",
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_distance=risk_distance,
            reward_distance=reward_distance,
            reward_risk_ratio=reward_risk_ratio,
            reasons=reasons,
        )

    # --------------------------------
    # NO TRADE
    # --------------------------------

    reasons.append(
        "Trend and momentum conditions did not "
        "produce a valid trade."
    )

    return TradeDecision(
        action="NO_TRADE",
        symbol=symbol,
        entry_price=None,
        stop_loss=None,
        take_profit=None,
        risk_distance=None,
        reward_distance=None,
        reward_risk_ratio=None,
        reasons=reasons,
    )