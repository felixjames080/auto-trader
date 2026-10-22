from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from app.adapters.base import HistoricalCandle


@dataclass(frozen=True)
class ScalpingSignal:
    symbol: str
    action: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward_ratio: float | None
    score: float
    reasons: list[str]


def _ema(values: list[float], period: int) -> list[float]:
    """Calculate an exponential moving average."""
    if period < 1:
        raise ValueError("EMA period must be positive.")
    if not values:
        return []

    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]

    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])

    return result


def _atr(candles: list[HistoricalCandle], period: int = 14) -> float:
    """Calculate simple-average ATR from true ranges."""
    if period < 1:
        raise ValueError("ATR period must be positive.")
    if len(candles) < period + 1:
        raise ValueError(f"At least {period + 1} candles are required for ATR.")

    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )

    return sum(true_ranges[-period:]) / period


def _validate_candles(
    candles: list[HistoricalCandle],
    minimum: int,
    timeframe_minutes: int,
) -> None:
    if len(candles) < minimum:
        raise ValueError(
            f"Need at least {minimum} candles; received {len(candles)}."
        )

    previous_timestamp = None
    for candle in candles:
        values = (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        if not all(isfinite(float(value)) for value in values):
            raise ValueError("Candles contain non-finite values.")

        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            raise ValueError("Candle prices must be positive.")

        if (
            candle.high < max(candle.open, candle.close)
            or candle.low > min(candle.open, candle.close)
            or candle.high < candle.low
        ):
            raise ValueError("Candle OHLC values are inconsistent.")

        if previous_timestamp is not None:
            if candle.timestamp <= previous_timestamp:
                raise ValueError(
                    "Candles must be ordered by increasing timestamp."
                )
            elapsed = (candle.timestamp - previous_timestamp).total_seconds()
            if elapsed < timeframe_minutes * 60:
                raise ValueError(
                    "Duplicate or overlapping candle timestamps detected."
                )

        previous_timestamp = candle.timestamp


def generate_scalping_signal(
    symbol: str,
    m1_candles: list[HistoricalCandle],
    m5_candles: list[HistoricalCandle],
    bid: float,
    ask: float,
    spread: float,
    max_spread: float,
    atr_stop_multiplier: float = 1.5,
    reward_risk_ratio: float = 1.5,
    min_score: float = 60.0,
    max_spread_atr_ratio: float = 0.20,
) -> ScalpingSignal:
    """
    Generate a candidate signal from completed M1 and M5 candles.

    `spread` and `max_spread` must both be price units, not points.
    This function never submits, modifies, or closes orders.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Symbol must not be empty.")

    if not all(
        isfinite(float(value))
        for value in (bid, ask, spread, max_spread, atr_stop_multiplier,
                      reward_risk_ratio, min_score, max_spread_atr_ratio)
    ):
        raise ValueError("Quote, risk, and threshold values must be finite.")

    if bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError("Invalid bid/ask quote.")
    if spread < 0 or max_spread < 0:
        raise ValueError("Spread values cannot be negative.")
    if atr_stop_multiplier <= 0 or reward_risk_ratio <= 0:
        raise ValueError("Stop multiplier and reward/risk ratio must be positive.")
    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100.")
    if max_spread_atr_ratio <= 0:
        raise ValueError("max_spread_atr_ratio must be positive.")

    observed_spread = ask - bid
    # Prefer the spread calculated from the actual quote.
    if abs(observed_spread - spread) > max(1e-8, observed_spread * 0.05):
        spread = observed_spread

    def no_trade(reason: str) -> ScalpingSignal:
        return ScalpingSignal(
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward_ratio=None,
            score=0.0,
            reasons=[reason],
        )

    if spread > max_spread:
        return no_trade("Spread exceeds the configured maximum.")

    _validate_candles(m1_candles, 30, 1)
    _validate_candles(m5_candles, 30, 5)

    m1_closes = [c.close for c in m1_candles]
    m5_closes = [c.close for c in m5_candles]

    m1_ema9 = _ema(m1_closes, 9)
    m1_ema21 = _ema(m1_closes, 21)
    m5_ema9 = _ema(m5_closes, 9)
    m5_ema21 = _ema(m5_closes, 21)

    atr = _atr(m1_candles, 14)
    if not isfinite(atr) or atr <= 0:
        return no_trade("ATR is invalid or too small.")

    spread_atr_ratio = spread / atr
    if spread_atr_ratio > max_spread_atr_ratio:
        return no_trade(
            "Spread is too large relative to M1 volatility."
        )

    previous = m1_candles[-2]
    recent = m1_candles[-1]

    bullish_trend = m5_ema9[-1] > m5_ema21[-1]
    bearish_trend = m5_ema9[-1] < m5_ema21[-1]

    bullish_momentum = (
        m1_ema9[-1] > m1_ema21[-1]
        and recent.close > previous.high
        and recent.close > recent.open
    )
    bearish_momentum = (
        m1_ema9[-1] < m1_ema21[-1]
        and recent.close < previous.low
        and recent.close < recent.open
    )

    if bullish_trend and bullish_momentum:
        action = "BUY"
        entry = ask
        reasons = [
            "M5 EMA trend is bullish.",
            "M1 EMA alignment and bullish breakout candle confirmed.",
        ]
    elif bearish_trend and bearish_momentum:
        action = "SELL"
        entry = bid
        reasons = [
            "M5 EMA trend is bearish.",
            "M1 EMA alignment and bearish breakout candle confirmed.",
        ]
    else:
        return no_trade(
            "Trend and momentum conditions did not produce a valid trade."
        )

    stop_distance = atr_stop_multiplier * atr
    if not isfinite(stop_distance) or stop_distance <= spread:
        return no_trade("Stop distance is invalid or too small relative to spread.")

    if action == "BUY":
        stop = entry - stop_distance
        target = entry + reward_risk_ratio * stop_distance
    else:
        stop = entry + stop_distance
        target = entry - reward_risk_ratio * stop_distance

    if not all(isfinite(value) and value > 0 for value in (entry, stop, target)):
        return no_trade("Calculated entry, stop-loss, or take-profit is invalid.")

    candle_range = max(recent.high - recent.low, 1e-12)
    body_ratio = min(abs(recent.close - recent.open) / candle_range, 1.0)
    trend_separation = min(
        abs(m5_ema9[-1] - m5_ema21[-1]) / atr,
        1.0,
    )
    m1_separation = min(
        abs(m1_ema9[-1] - m1_ema21[-1]) / atr,
        1.0,
    )
    spread_quality = max(
        0.0,
        1.0 - spread_atr_ratio / max_spread_atr_ratio,
    )

    # Transparent 0–100 ranking heuristic, not a win probability.
    score = 35.0 * trend_separation
    score += 25.0 * m1_separation
    score += 25.0 * body_ratio
    score += 15.0 * spread_quality

    reasons.append(f"ATR-based stop distance: {stop_distance:.8g}.")
    reasons.append(f"Spread/ATR ratio: {spread_atr_ratio:.3f}.")
    reasons.append("Score is a ranking heuristic, not a win probability.")

    if score < min_score:
        return ScalpingSignal(
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward_ratio=None,
            score=round(score, 2),
            reasons=reasons + [
                f"Signal score {score:.2f} is below minimum {min_score:.2f}."
            ],
        )

    return ScalpingSignal(
        symbol=symbol,
        action=action,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        risk_reward_ratio=reward_risk_ratio,
        score=round(score, 2),
        reasons=reasons,
    )
