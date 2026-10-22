from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from app.adapters.mt5 import MT5MarketDataAdapter
from app.services.instrument_scanner import InstrumentScanner
from app.services.scalping_engine import generate_scalping_signal

logger = logging.getLogger(__name__)

FOREX_PAIRS = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
)


@dataclass
class ScanningResult:
    scanned_instruments: int
    forex_candidates: int
    analyzed_instruments: int
    buy_count: int
    sell_count: int
    no_trade_count: int
    signals: list[dict[str, Any]]
    rejections: list[dict[str, Any]]
    errors: list[str]


class ScalpingScanner:
    """Read-only forex scalping scanner. Never submits orders."""

    def __init__(
        self,
        max_spread_points: float = 30.0,
        lookback_days: int = 3,
        max_instruments: int = 20,
        min_score: float = 60.0,
        max_spread_atr_ratio: float = 0.20,
    ) -> None:
        if max_spread_points < 0:
            raise ValueError("max_spread_points cannot be negative.")
        if lookback_days < 1:
            raise ValueError("lookback_days must be at least 1.")
        if max_instruments < 1:
            raise ValueError("max_instruments must be at least 1.")
        if not 0 <= min_score <= 100:
            raise ValueError("min_score must be between 0 and 100.")
        if max_spread_atr_ratio <= 0:
            raise ValueError("max_spread_atr_ratio must be positive.")

        self.max_spread_points = float(max_spread_points)
        self.lookback_days = lookback_days
        self.max_instruments = max_instruments
        self.min_score = min_score
        self.max_spread_atr_ratio = max_spread_atr_ratio

        self.instrument_scanner = InstrumentScanner(
            max_spread_points=self.max_spread_points
        )
        self.market_data = MT5MarketDataAdapter()

    @staticmethod
    def _base_pair(symbol: str) -> str | None:
        """Recognize a major forex pair with a broker suffix."""
        normalized = symbol.upper()
        for pair in FOREX_PAIRS:
            if normalized.startswith(pair):
                suffix = normalized[len(pair):]
                if not suffix or not suffix[0].isalpha():
                    return pair
        return None

    def scan(self) -> ScanningResult:
        errors: list[str] = []
        signals: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        analyzed = 0

        instruments = self.instrument_scanner.scan()

        # Keep only the seven requested forex pairs and valid trade modes.
        forex = [
            item for item in instruments
            if item.trade_allowed and self._base_pair(item.symbol)
        ]

        # Prefer the lowest-spread symbol for each pair, including suffixes.
        selected: dict[str, Any] = {}
        for item in forex:
            pair = self._base_pair(item.symbol)
            if pair is None:
                continue
            previous = selected.get(pair)
            if previous is None or item.spread_points < previous.spread_points:
                selected[pair] = item

        candidates = sorted(
            selected.values(),
            key=lambda item: item.spread_points,
        )[:self.max_instruments]

        for instrument in candidates:
            try:
                m1 = self.market_data.get_historical_data(
                    symbol=instrument.symbol,
                    timeframe_minutes=1,
                    lookback_days=self.lookback_days,
                )
                m5 = self.market_data.get_historical_data(
                    symbol=instrument.symbol,
                    timeframe_minutes=5,
                    lookback_days=self.lookback_days,
                )

                if len(m1) < 30 or len(m5) < 30:
                    rejections.append({
                        "symbol": instrument.symbol,
                        "reason": (
                            f"Insufficient candles: M1={len(m1)}, "
                            f"M5={len(m5)}."
                        ),
                    })
                    continue

                max_spread_price = (
                    self.max_spread_points * instrument.point
                )

                signal = generate_scalping_signal(
                    symbol=instrument.symbol,
                    m1_candles=m1,
                    m5_candles=m5,
                    bid=instrument.bid,
                    ask=instrument.ask,
                    spread=instrument.spread,
                    max_spread=max_spread_price,
                    min_score=self.min_score,
                    max_spread_atr_ratio=self.max_spread_atr_ratio,
                )

                analyzed += 1

                if signal.action in ("BUY", "SELL"):
                    result = asdict(signal)
                    result["spread_points"] = instrument.spread_points
                    signals.append(result)
                else:
                    rejections.append({
                        "symbol": instrument.symbol,
                        "spread_points": round(instrument.spread_points, 2),
                        "score": signal.score,
                        "reason": "; ".join(signal.reasons),
                    })

            except (RuntimeError, ValueError, TypeError) as exc:
                message = f"{instrument.symbol}: {exc}"
                logger.warning(message)
                errors.append(message)

        signals.sort(key=lambda item: item["score"], reverse=True)
        counts = Counter(item["action"] for item in signals)

        return ScanningResult(
            scanned_instruments=len(instruments),
            forex_candidates=len(candidates),
            analyzed_instruments=analyzed,
            buy_count=counts["BUY"],
            sell_count=counts["SELL"],
            no_trade_count=len(rejections),
            signals=signals,
            rejections=rejections,
            errors=errors,
        )


if __name__ == "__main__":
    import json

    scanner = ScalpingScanner(
        max_spread_points=30,
        lookback_days=3,
        max_instruments=20,
        min_score=60,
        max_spread_atr_ratio=0.20,
    )
    result = scanner.scan()
    print(json.dumps(asdict(result), indent=2, default=str))
