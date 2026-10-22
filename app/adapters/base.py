from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class MarketQuote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime


@dataclass
class HistoricalCandle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataAdapter(ABC):
    """
    Standard interface for market-data providers.

    The strategy engine talks to this interface rather than
    directly to a particular broker or data provider.
    """

    @abstractmethod
    def get_quote(self, symbol: str) -> MarketQuote:
        """Return the current market quote."""
        raise NotImplementedError

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        timeframe_minutes: int,
        lookback_days: int,
    ) -> list[HistoricalCandle]:
        """Return timestamped historical candles."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """Return whether the data connection is healthy."""
        raise NotImplementedError
    