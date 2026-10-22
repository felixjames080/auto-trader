from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import random


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class DataQualityResult:
    healthy: bool
    errors: list[str]
    warnings: list[str]


def generate_demo_candles(
    symbol: str,
    timeframe_minutes: int = 15,
    lookback_days: int = 30,
    seed: int = 42,
) -> list[Candle]:
    """
    Generate deterministic synthetic candles for development/testing.

    This is NOT real market data and must never be used for live trading.
    """

    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be greater than zero")

    if lookback_days <= 0:
        raise ValueError("lookback_days must be greater than zero")

    candles_per_day = (24 * 60) // timeframe_minutes
    total_candles = candles_per_day * lookback_days

    rng = random.Random(seed)

    # Development-only starting prices.
    base_prices = {
        "EURUSD": 1.0800,
        "GBPUSD": 1.2700,
        "USDJPY": 150.00,
        "XAUUSD": 2400.00,
    }

    price = base_prices.get(symbol.upper(), 100.0)

    now = datetime.now(timezone.utc)

    # Align the starting timestamp to the requested timeframe.
    start = now - timedelta(minutes=timeframe_minutes * total_candles)

    candles: list[Candle] = []

    for i in range(total_candles):
        timestamp = start + timedelta(
            minutes=i * timeframe_minutes
        )

        previous_close = price

        # Synthetic movement.
        drift = math.sin(i / 35.0) * 0.0004
        noise = rng.gauss(0, 0.001)

        percentage_move = drift + noise

        close = previous_close * (1 + percentage_move)

        high = max(previous_close, close) * (
            1 + abs(rng.gauss(0, 0.0005))
        )

        low = min(previous_close, close) * (
            1 - abs(rng.gauss(0, 0.0005))
        )

        volume = max(
            1.0,
            rng.gauss(1000, 150),
        )

        candles.append(
            Candle(
                timestamp=timestamp,
                open=previous_close,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )

        price = close

    return candles


def validate_candles(
    candles: list[Candle],
    timeframe_minutes: int,
    max_stale_minutes: int = 30,
) -> DataQualityResult:
    """
    Validate timestamp ordering, OHLC consistency,
    missing candles and data freshness.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if not candles:
        return DataQualityResult(
            healthy=False,
            errors=["No market data available."],
            warnings=[],
        )

    expected_delta = timedelta(minutes=timeframe_minutes)

    # Check timestamp ordering and candle integrity.
    for index, candle in enumerate(candles):

        if candle.high < max(candle.open, candle.close):
            errors.append(
                f"Invalid high at candle {index}."
            )

        if candle.low > min(candle.open, candle.close):
            errors.append(
                f"Invalid low at candle {index}."
            )

        if candle.low > candle.high:
            errors.append(
                f"Low is greater than high at candle {index}."
            )

        if candle.volume < 0:
            errors.append(
                f"Negative volume at candle {index}."
            )

        if index > 0:
            previous = candles[index - 1]

            if candle.timestamp <= previous.timestamp:
                errors.append(
                    f"Timestamps are not strictly increasing "
                    f"at candle {index}."
                )

            actual_delta = candle.timestamp - previous.timestamp

            if actual_delta != expected_delta:
                errors.append(
                    f"Missing or inconsistent candle interval "
                    f"between {previous.timestamp} and "
                    f"{candle.timestamp}."
                )

    # Check freshness.
    latest_timestamp = candles[-1].timestamp
    now = datetime.now(timezone.utc)

    age_minutes = (
        now - latest_timestamp
    ).total_seconds() / 60

    if age_minutes > max_stale_minutes:
        errors.append(
            f"Market data is stale by approximately "
            f"{age_minutes:.1f} minutes."
        )

    # Check for suspiciously old timestamps.
    if latest_timestamp > now + timedelta(minutes=2):
        errors.append(
            "Latest market-data timestamp is in the future."
        )

    return DataQualityResult(
        healthy=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )