
from datetime import datetime, timedelta, timezone
from threading import RLock

import MetaTrader5 as mt5

from app.adapters.base import (
    HistoricalCandle,
    MarketDataAdapter,
    MarketQuote,
)
from app.services.market_data import generate_demo_candles


DEFAULT_MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# MT5 terminal operations are serialized to avoid concurrent calls
# from multiple FastAPI requests interfering with each other.
_MT5_LOCK = RLock()


def _timeframe_constant(timeframe_minutes: int) -> int:
    """Convert supported minute intervals into MT5 timeframes."""
    mapping = {
        1: mt5.TIMEFRAME_M1,
        2: mt5.TIMEFRAME_M2,
        3: mt5.TIMEFRAME_M3,
        4: mt5.TIMEFRAME_M4,
        5: mt5.TIMEFRAME_M5,
        6: mt5.TIMEFRAME_M6,
        10: mt5.TIMEFRAME_M10,
        12: mt5.TIMEFRAME_M12,
        15: mt5.TIMEFRAME_M15,
        20: mt5.TIMEFRAME_M20,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
        120: mt5.TIMEFRAME_H2,
        180: mt5.TIMEFRAME_H3,
        240: mt5.TIMEFRAME_H4,
        360: mt5.TIMEFRAME_H6,
        480: mt5.TIMEFRAME_H8,
        720: mt5.TIMEFRAME_H12,
        1440: mt5.TIMEFRAME_D1,
    }

    if timeframe_minutes not in mapping:
        raise ValueError(
            f"Unsupported MT5 timeframe: {timeframe_minutes} minutes."
        )

    return mapping[timeframe_minutes]


class DevelopmentMarketDataAdapter(MarketDataAdapter):
    """
    Development-only market-data adapter.

    Generates synthetic candles for development and testing.
    Never use this adapter as evidence of real trading profitability.
    """

    def get_quote(self, symbol: str) -> MarketQuote:
        candles = generate_demo_candles(
            symbol=symbol.upper(),
            timeframe_minutes=15,
            lookback_days=1,
        )

        latest = candles[-1]

        return MarketQuote(
            symbol=symbol.upper(),
            bid=latest.close,
            ask=latest.close,
            timestamp=datetime.now(timezone.utc),
        )

    def get_historical_data(
        self,
        symbol: str,
        timeframe_minutes: int,
        lookback_days: int,
    ) -> list[HistoricalCandle]:
        candles = generate_demo_candles(
            symbol=symbol.upper(),
            timeframe_minutes=timeframe_minutes,
            lookback_days=lookback_days,
        )

        return [
            HistoricalCandle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for candle in candles
        ]

    def health_check(self) -> bool:
        # This indicates that the development adapter is available,
        # not that a broker connection exists.
        return True


class MT5MarketDataAdapter(MarketDataAdapter):
    """
    Read-only market-data adapter for a running MetaTrader 5 terminal.

    This adapter retrieves quotes and historical candles. It does not
    submit, modify, or close trading orders.
    """

    def __init__(
        self,
        terminal_path: str = DEFAULT_MT5_PATH,
        timeout_ms: int = 60000,
        symbol_aliases: dict[str, str] | None = None,
    ):
        self.terminal_path = terminal_path
        self.timeout_ms = timeout_ms
        self.symbol_aliases = {
            key.upper(): value for key, value in (symbol_aliases or {}).items()
        }

    def _resolve_symbol(self, symbol: str) -> str:
        requested = symbol.strip()
        if not requested:
            raise ValueError("Symbol must not be empty.")

        # Allow an explicitly configured broker-specific symbol name.
        actual = self.symbol_aliases.get(requested.upper(), requested)

        if not mt5.symbol_select(actual, True):
            raise RuntimeError(
                f"Could not select MT5 symbol '{actual}'. "
                f"Check the broker's exact symbol name and Market Watch. "
                f"MT5 error: {mt5.last_error()}"
            )

        info = mt5.symbol_info(actual)
        if info is None:
            raise RuntimeError(
                f"MT5 returned no symbol information for '{actual}'. "
                f"MT5 error: {mt5.last_error()}"
            )

        return actual

    def _initialize(self) -> None:
        # Initialize the terminal for each operation. This avoids relying
        # on a connection left open by a previous request.
        if not mt5.initialize(
            path=self.terminal_path,
            timeout=self.timeout_ms,
        ):
            raise RuntimeError(
                f"Could not initialize MetaTrader 5. "
                f"MT5 error: {mt5.last_error()}"
            )

    def get_quote(self, symbol: str) -> MarketQuote:
        with _MT5_LOCK:
            try:
                self._initialize()
                actual_symbol = self._resolve_symbol(symbol)
                tick = mt5.symbol_info_tick(actual_symbol)

                if tick is None:
                    raise RuntimeError(
                        f"No current quote for '{actual_symbol}'. "
                        f"MT5 error: {mt5.last_error()}"
                    )

                if tick.bid <= 0 or tick.ask <= 0 or tick.ask < tick.bid:
                    raise RuntimeError(
                        f"MT5 returned an invalid bid/ask for "
                        f"'{actual_symbol}'."
                    )

                return MarketQuote(
                    symbol=actual_symbol,
                    bid=float(tick.bid),
                    ask=float(tick.ask),
                    timestamp=datetime.fromtimestamp(
                        int(tick.time),
                        tz=timezone.utc,
                    ),
                )
            finally:
                mt5.shutdown()

    def get_historical_data(
        self,
        symbol: str,
        timeframe_minutes: int,
        lookback_days: int,
    ) -> list[HistoricalCandle]:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be greater than zero.")

        if lookback_days > 3650:
            raise ValueError("lookback_days must not exceed 3650.")

        timeframe = _timeframe_constant(timeframe_minutes)

        with _MT5_LOCK:
            try:
                self._initialize()
                actual_symbol = self._resolve_symbol(symbol)

                # MT5 stores candle times as Unix timestamps. Use UTC
                # consistently when defining the requested date range.
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(days=lookback_days)

                rates = mt5.copy_rates_range(
                    actual_symbol,
                    timeframe,
                    start_time,
                    end_time,
                )

                if rates is None:
                    raise RuntimeError(
                        f"MT5 historical-data request failed for "
                        f"'{actual_symbol}'. MT5 error: {mt5.last_error()}"
                    )

                if len(rates) == 0:
                    raise RuntimeError(
                        f"MT5 returned no historical candles for "
                        f"'{actual_symbol}' over the last "
                        f"{lookback_days} days."
                    )

                candles: list[HistoricalCandle] = []

                for rate in rates:
                    timestamp = datetime.fromtimestamp(
                        int(rate["time"]),
                        tz=timezone.utc,
                    )

                    candle = HistoricalCandle(
                        timestamp=timestamp,
                        open=float(rate["open"]),
                        high=float(rate["high"]),
                        low=float(rate["low"]),
                        close=float(rate["close"]),
                        volume=float(rate["tick_volume"]),
                    )

                    if (
                        candle.open <= 0
                        or candle.high <= 0
                        or candle.low <= 0
                        or candle.close <= 0
                        or candle.high < max(candle.open, candle.close)
                        or candle.low > min(candle.open, candle.close)
                        or candle.high < candle.low
                    ):
                        raise RuntimeError(
                            f"Invalid OHLC candle received at "
                            f"{timestamp.isoformat()} for '{actual_symbol}'."
                        )

                    candles.append(candle)

                # MT5 may include the currently forming candle. Exclude it
                # so a backtest cannot use an unfinished bar.
                current_bar_start = int(
                    end_time.timestamp() // (timeframe_minutes * 60)
                ) * timeframe_minutes * 60

                candles = [
                    candle
                    for candle in candles
                    if int(candle.timestamp.timestamp()) < current_bar_start
                ]

                if not candles:
                    raise RuntimeError(
                        f"No completed candles were available for "
                        f"'{actual_symbol}'."
                    )

                # Ensure deterministic chronological order and remove
                # any duplicate timestamps.
                candles_by_time = {
                    candle.timestamp: candle for candle in candles
                }

                return [
                    candles_by_time[timestamp]
                    for timestamp in sorted(candles_by_time)
                ]
            finally:
                mt5.shutdown()

    def health_check(self) -> bool:
        with _MT5_LOCK:
            try:
                self._initialize()
                info = mt5.terminal_info()
                return info is not None and bool(info.connected)
            except Exception:
                return False
            finally:
                mt5.shutdown()

