from __future__ import annotations

import logging
from dataclasses import dataclass

import MetaTrader5 as mt5

from app.adapters.mt5 import MT5MarketDataAdapter, _MT5_LOCK

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScannedInstrument:
    symbol: str
    bid: float
    ask: float
    spread: float
    spread_points: float
    digits: int
    point: float
    trade_allowed: bool


class InstrumentScanner:
    """
    Discover MT5 instruments and return valid quotes.

    This module is read-only: it never submits or modifies orders.
    """

    def __init__(
        self,
        max_spread_points: float = 30.0,
        terminal_path: str | None = None,
    ) -> None:
        if max_spread_points < 0:
            raise ValueError("max_spread_points cannot be negative.")

        self.max_spread_points = float(max_spread_points)

        self.adapter = (
            MT5MarketDataAdapter(terminal_path=terminal_path)
            if terminal_path
            else MT5MarketDataAdapter()
        )

    def scan(self) -> list[ScannedInstrument]:
        """
        Scan symbols visible to the MT5 terminal.

        Instruments with missing/invalid quotes or spreads above the
        configured limit are excluded. Results are sorted by spread.
        """
        results: list[ScannedInstrument] = []

        with _MT5_LOCK:
            try:
                self.adapter._initialize()

                symbols = mt5.symbols_get()

                if symbols is None:
                    raise RuntimeError(
                        f"MT5 could not list symbols: {mt5.last_error()}"
                    )

                for item in symbols:
                    symbol = item.name

                    try:
                        if not mt5.symbol_select(symbol, True):
                            logger.debug(
                                "Skipping %s: could not select symbol.",
                                symbol,
                            )
                            continue

                        info = mt5.symbol_info(symbol)
                        tick = mt5.symbol_info_tick(symbol)

                        if info is None or tick is None:
                            continue

                        bid = float(tick.bid)
                        ask = float(tick.ask)
                        point = float(info.point)

                        if (
                            bid <= 0
                            or ask <= 0
                            or ask < bid
                            or point <= 0
                        ):
                            continue

                        spread = ask - bid
                        spread_points = spread / point

                        if spread_points > self.max_spread_points:
                            continue

                        # MT5 trade_mode:
                        # 0 = disabled, 1 = long-only, 2 = short-only,
                        # 3 = close-only, 4 = full trading.
                        trade_mode = int(info.trade_mode)
                        trade_allowed = trade_mode in (1, 2, 4)

                        results.append(
                            ScannedInstrument(
                                symbol=symbol,
                                bid=bid,
                                ask=ask,
                                spread=spread,
                                spread_points=spread_points,
                                digits=int(info.digits),
                                point=point,
                                trade_allowed=trade_allowed,
                            )
                        )

                    except (AttributeError, TypeError, ValueError) as exc:
                        logger.debug(
                            "Skipping invalid symbol %s: %s",
                            symbol,
                            exc,
                        )

                results.sort(key=lambda instrument: instrument.spread_points)
                return results

            finally:
                mt5.shutdown()
                