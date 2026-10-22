from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from app.adapters.mt5 import MT5MarketDataAdapter
from app.services.instrument_scanner import InstrumentScanner
from app.services.scalping_engine import generate_scalping_signal

logger = logging.getLogger(__name__)


@dataclass
class ScanningResult:
    scanned_instruments: int
    analyzed_instruments: int
    buy_count: int
    sell_count: int
    no_trade_count: int
    signals: list[dict[str, Any]]
    rejections: list[dict[str, Any]]
    errors: list[str]


class ScalpingScanner:
    """Read-only MT5 market scanner. Never submits orders."""

    def __init__(
        self,
        max_spread_points: float = 30.0,
        lookback_days: int = 3,
        max_instruments: int = 10,
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

    def scan(self) -> ScanningResult:
        errors: list[str] = []
        signals: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        analyzed = 0

        instruments = self.instrument_scanner.scan()
        candidates = [
            item for item in instruments if item.trade_allowed
        ][:self.max_instruments]

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
                            "Insufficient candles: "
                            f"M1={len(m1)}, M5={len(m5)}."
                        ),
                    })
                    continue

                # Convert configured maximum spread from MT5 points
                # into price units, which the engine expects.
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

                if signal.action == "BUY":
                    signals.append(asdict(signal))
                elif signal.action == "SELL":
                    signals.append(asdict(signal))
                else:
                    reason = "; ".join(signal.reasons)
                    rejections.append({
                        "symbol": instrument.symbol,
                        "spread_points": instrument.spread_points,
                        "reason": reason,
                        "score": signal.score,
                    })

            except (RuntimeError, ValueError, TypeError) as exc:
                message = f"{instrument.symbol}: {exc}"
                logger.warning(message)
                errors.append(message)

        signals.sort(key=lambda item: item["score"], reverse=True)

        counts = Counter(item["action"] for item in signals)

        return ScanningResult(
            scanned_instruments=len(instruments),
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

    try:
        scanner = ScalpingScanner(
            max_spread_points=30,
            lookback_days=3,
            max_instruments=10,
            min_score=60,
            max_spread_atr_ratio=0.20,
        )
        result = scanner.scan()
        print(json.dumps(asdict(result), indent=2, default=str))
    except Exception as exc:
        raise SystemExit(f"Scalping scan failed: {exc}")
