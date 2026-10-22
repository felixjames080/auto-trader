from app.services.position_sizing import InstrumentSpec
from app.adapters.mt5 import MT5MarketDataAdapter, _MT5_LOCK

import MetaTrader5 as mt5


def get_instrument_spec(symbol: str) -> InstrumentSpec | None:
    """
    Retrieve instrument specifications from MT5.

    Uses the configured MT5 terminal path and serializes terminal
    operations using the adapter's existing lock.
    """

    symbol = symbol.strip().upper()

    if not symbol:
        return None

    adapter = MT5MarketDataAdapter()

    with _MT5_LOCK:
        try:
            adapter._initialize()

            actual_symbol = adapter._resolve_symbol(symbol)
            info = mt5.symbol_info(actual_symbol)

            if info is None:
                return None

            tick_size = float(info.trade_tick_size)
            tick_value = float(info.trade_tick_value)
            volume_min = float(info.volume_min)
            volume_max = float(info.volume_max)
            volume_step = float(info.volume_step)

            if (
                tick_size <= 0
                or tick_value <= 0
                or volume_min <= 0
                or volume_max < volume_min
                or volume_step <= 0
            ):
                return None

            return InstrumentSpec(
                symbol=actual_symbol,
                tick_size=tick_size,
                tick_value=tick_value,
                volume_min=volume_min,
                volume_max=volume_max,
                volume_step=volume_step,
            )

        except (RuntimeError, AttributeError, TypeError, ValueError):
            return None

        finally:
            mt5.shutdown()