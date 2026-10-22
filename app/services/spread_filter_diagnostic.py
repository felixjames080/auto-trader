from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from app.services.scalping_scanner import ScalpingScanner
from app.services.scalping_engine import _atr


@dataclass
class TimeframeDiagnostic:
    timeframe_minutes: int
    candles_available: int
    windows_tested: int
    windows_passing_filter: int
    pass_percentage: float
    median_spread_atr_ratio: float | None
    minimum_spread_atr_ratio: float | None
    maximum_spread_atr_ratio: float | None


def analyze_timeframe(
    candles: list[Any],
    timeframe_minutes: int,
    spread: float,
    threshold: float = 0.20,
    period: int = 14,
) -> TimeframeDiagnostic:
    ratios: list[float] = []

    # Each sample uses only the candle window available at that point.
    for end in range(period + 1, len(candles) + 1):
        window = candles[end - period - 1:end]

        try:
            atr = _atr(window, period)
        except (ValueError, TypeError):
            continue

        if atr > 0:
            ratios.append(spread / atr)

    passing = sum(ratio <= threshold for ratio in ratios)

    return TimeframeDiagnostic(
        timeframe_minutes=timeframe_minutes,
        candles_available=len(candles),
        windows_tested=len(ratios),
        windows_passing_filter=passing,
        pass_percentage=round(
            100.0 * passing / len(ratios), 2
        ) if ratios else 0.0,
        median_spread_atr_ratio=round(median(ratios), 4)
        if ratios else None,
        minimum_spread_atr_ratio=round(min(ratios), 4)
        if ratios else None,
        maximum_spread_atr_ratio=round(max(ratios), 4)
        if ratios else None,
    )


def main() -> None:
    threshold = 0.20

    scanner = ScalpingScanner(
        max_spread_points=30,
        lookback_days=3,
        max_instruments=20,
    )

    instruments = scanner.instrument_scanner.scan()
    selected = {}

    for item in instruments:
        pair = scanner._base_pair(item.symbol)

        if not item.trade_allowed or pair is None:
            continue

        previous = selected.get(pair)

        if (
            previous is None
            or item.spread_points < previous.spread_points
        ):
            selected[pair] = item

    report = {
        "mode": "DIAGNOSTIC_ONLY",
        "orders_enabled": False,
        "threshold": threshold,
        "historical_spread_warning": (
            "The current spread is applied to historical ATR values. "
            "Historical spreads, commissions and slippage are not known "
            "from this calculation. Results are diagnostic, not a "
            "backtest or proof of profitability."
        ),
        "pairs": [],
        "errors": [],
    }

    for pair, item in sorted(selected.items()):
        try:
            m1 = scanner.market_data.get_historical_data(
                symbol=item.symbol,
                timeframe_minutes=1,
                lookback_days=3,
            )
            m5 = scanner.market_data.get_historical_data(
                symbol=item.symbol,
                timeframe_minutes=5,
                lookback_days=3,
            )

            result_m1 = analyze_timeframe(
                m1, 1, item.spread, threshold
            )
            result_m5 = analyze_timeframe(
                m5, 5, item.spread, threshold
            )

            report["pairs"].append({
                "base_pair": pair,
                "mt5_symbol": item.symbol,
                "current_spread_points": round(
                    item.spread_points, 2
                ),
                "m1_filter": asdict(result_m1),
                "m5_filter": asdict(result_m5),
            })

        except Exception as exc:
            report["errors"].append(
                f"{item.symbol}: {type(exc).__name__}: {exc}"
            )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
