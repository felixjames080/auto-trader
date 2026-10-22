from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.services.instruments import get_instrument_spec
from app.services.market_data import Candle
from app.services.position_sizing import calculate_position_size
from app.services.trade_decision import generate_trade_decision


@dataclass
class BacktestTrade:
    trade_number: int
    symbol: str
    side: str
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    requested_entry: float
    actual_entry: float
    stop_loss: float
    take_profit: float
    quantity: float
    gross_pnl: float
    execution_cost: float
    net_pnl: float
    exit_reason: str
    risk_amount: float
    risk_percent: float
    reward_risk_ratio: float
    # New fields have defaults to reduce disruption to existing callers.
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    commission_cost: float = 0.0
    holding_minutes: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    timeframe_minutes: int
    initial_equity: float
    final_equity: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_percent: float
    total_net_pnl: float
    average_trade_pnl: float
    largest_win: float
    largest_loss: float
    max_drawdown_amount: float
    max_drawdown_percent: float
    profit_factor: float
    trades: list[BacktestTrade] = field(default_factory=list)
    # Additional diagnostics.
    total_spread_cost: float = 0.0
    total_slippage_cost: float = 0.0
    total_commission_cost: float = 0.0
    total_execution_cost: float = 0.0
    expectancy_r: Optional[float] = None
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    average_holding_minutes: float = 0.0
    side_performance: dict = field(default_factory=dict)
    monthly_performance: list[dict] = field(default_factory=list)


def _calculate_trade_pnl(
    side: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    tick_size: float,
    tick_value: float,
) -> float:
    if tick_size <= 0 or tick_value <= 0:
        raise ValueError("Instrument tick size and tick value must be greater than zero.")
    if quantity <= 0:
        raise ValueError("Trade quantity must be greater than zero.")

    side = side.upper()
    if side == "BUY":
        price_difference = exit_price - entry_price
    elif side == "SELL":
        price_difference = entry_price - exit_price
    else:
        raise ValueError("Side must be BUY or SELL.")

    return (price_difference / tick_size) * tick_value * quantity


def _price_cost_to_account_currency(
    requested_price: float,
    executed_price: float,
    quantity: float,
    tick_size: float,
    tick_value: float,
) -> float:
    """Convert price slippage/spread impact into the instrument's account-currency estimate."""
    if tick_size <= 0 or tick_value <= 0:
        raise ValueError("Instrument tick size and tick value must be greater than zero.")
    return abs(executed_price - requested_price) / tick_size * tick_value * quantity


def _apply_entry_cost(
    side: str,
    requested_price: float,
    spread: float,
    slippage: float,
) -> float:
    half_spread = abs(spread) / 2
    slippage = abs(slippage)
    side = side.upper()

    if side == "BUY":
        return requested_price + half_spread + slippage
    if side == "SELL":
        return requested_price - half_spread - slippage
    raise ValueError("Side must be BUY or SELL.")


def _apply_exit_cost(
    side: str,
    requested_price: float,
    spread: float,
    slippage: float,
) -> float:
    half_spread = abs(spread) / 2
    slippage = abs(slippage)
    side = side.upper()

    if side == "BUY":
        return requested_price - half_spread - slippage
    if side == "SELL":
        return requested_price + half_spread + slippage
    raise ValueError("Side must be BUY or SELL.")


def _determine_exit(
    side: str,
    candle: Candle,
    stop_loss: float,
    take_profit: float,
) -> tuple[Optional[float], Optional[str]]:
    """
    Determine an OHLC exit using conservative assumptions.

    Adverse gaps through a stop are filled at the candle open, not at the
    unreachable stop price. A favorable gap through a take-profit is filled
    at the target price (a conservative limit-order assumption). If both
    levels are touched intrabar, assume the stop-loss happened first.
    """
    side = side.upper()

    if side == "BUY":
        # A BUY position loses when price falls. An open below the stop is
        # an adverse gap and must not be filled at the better stop price.
        if candle.open <= stop_loss:
            return candle.open, "STOP_LOSS_GAP"
        if candle.open >= take_profit:
            return take_profit, "TAKE_PROFIT"
        hit_stop = candle.low <= stop_loss
        hit_target = candle.high >= take_profit
    elif side == "SELL":
        # A SELL position loses when price rises.
        if candle.open >= stop_loss:
            return candle.open, "STOP_LOSS_GAP"
        if candle.open <= take_profit:
            return take_profit, "TAKE_PROFIT"
        hit_stop = candle.high >= stop_loss
        hit_target = candle.low <= take_profit
    else:
        raise ValueError("Side must be BUY or SELL.")

    # Conservative intrabar assumption: if both levels are touched,
    # assume the stop-loss happened first.
    if hit_stop:
        return stop_loss, "STOP_LOSS"
    if hit_target:
        return take_profit, "TAKE_PROFIT"
    return None, None


def _max_consecutive(trades: list[BacktestTrade], is_win: bool) -> int:
    longest = current = 0
    for trade in trades:
        matched = trade.net_pnl > 0 if is_win else trade.net_pnl < 0
        if matched:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def run_backtest(
    candles: list[Candle],
    symbol: str = "EURUSD",
    timeframe_minutes: int = 15,
    initial_equity: float = 10_000.0,
    max_risk_percent: float = 1.0,
    max_position_size: float = 1.0,
    max_daily_loss_percent: float = 5.0,
    spread: float = 0.00010,
    entry_slippage: float = 0.00002,
    exit_slippage: float = 0.00002,
    commission_per_unit: float = 0.0,
) -> BacktestResult:
    """
    Run a development backtest.

    spread, entry_slippage, and exit_slippage are expressed in PRICE units,
    not pips. commission_per_unit is the round-trip commission amount per
    unit of quantity. Set it using the broker's actual commission convention
    before interpreting results for a real account.

    P&L is measured from requested/reference entry and exit prices. Spread,
    slippage, and commission are deducted exactly once as explicit costs.
    Stop/target distances are anchored to the requested next-candle entry;
    actual_entry is retained as an execution estimate for reporting only.
    This is still a simplified OHLC model, not a full fill simulator or proof
    of live profitability.
    """
    if initial_equity <= 0:
        raise ValueError("Initial equity must be greater than zero.")
    if not candles:
        raise ValueError("Backtest requires at least one candle.")
    if max_risk_percent <= 0:
        raise ValueError("Maximum risk percent must be greater than zero.")
    if max_position_size <= 0:
        raise ValueError("Maximum position size must be greater than zero.")
    if max_daily_loss_percent < 0:
        raise ValueError("Maximum daily loss percent cannot be negative.")
    if spread < 0 or entry_slippage < 0 or exit_slippage < 0:
        raise ValueError("Spread and slippage inputs cannot be negative.")
    if commission_per_unit < 0:
        raise ValueError("Commission per unit cannot be negative.")

    instrument = get_instrument_spec(symbol)
    if instrument is None:
        raise ValueError(f"No instrument specification exists for {symbol}.")
    if instrument.tick_size <= 0 or instrument.tick_value <= 0:
        raise ValueError("Instrument tick size and tick value must be greater than zero.")

    equity = initial_equity
    trades: list[BacktestTrade] = []
    equity_curve = [equity]
    current_date = None
    daily_start_equity = equity
    minimum_strategy_candles = 50
    i = minimum_strategy_candles - 1

    while i < len(candles) - 1:
        signal_candles = candles[: i + 1]
        decision = generate_trade_decision(candles=signal_candles, symbol=symbol)

        if (
            decision.action == "NO_TRADE"
            or decision.entry_price is None
            or decision.stop_loss is None
            or decision.take_profit is None
        ):
            i += 1
            continue

        side = decision.action.upper()
        if side not in {"BUY", "SELL"}:
            i += 1
            continue

        signal_candle = candles[i]
        entry_candle = candles[i + 1]
        candle_date = entry_candle.timestamp.date()

        if current_date != candle_date:
            current_date = candle_date
            daily_start_equity = equity

        daily_loss_amount = max(0.0, daily_start_equity - equity)
        daily_loss_percent = (
            daily_loss_amount / daily_start_equity * 100
            if daily_start_equity > 0
            else 0.0
        )
        if daily_loss_percent >= max_daily_loss_percent:
            i += 1
            continue

        # Signal is formed from completed candles; execution starts next candle.
        # Use the next candle's open as the common reference for levels, sizing,
        # and gross P&L. Keep estimated execution price for diagnostics only;
        # spread/slippage are deducted explicitly below, exactly once.
        requested_entry = entry_candle.open
        actual_entry = _apply_entry_cost(
            side=side,
            requested_price=requested_entry,
            spread=spread,
            slippage=entry_slippage,
        )

        risk_distance = abs(decision.entry_price - decision.stop_loss)
        reward_distance = abs(decision.take_profit - decision.entry_price)
        if risk_distance <= 0 or reward_distance <= 0:
            i += 1
            continue

        if side == "BUY":
            stop_loss = requested_entry - risk_distance
            take_profit = requested_entry + reward_distance
        else:
            stop_loss = requested_entry + risk_distance
            take_profit = requested_entry - reward_distance

        sizing = calculate_position_size(
            account_equity=equity,
            entry_price=requested_entry,
            stop_loss=stop_loss,
            max_risk_percent=max_risk_percent,
            instrument=instrument,
            max_position_size=min(max_position_size, instrument.volume_max),
        )
        if not sizing.approved or sizing.quantity <= 0:
            i += 1
            continue

        quantity = sizing.quantity
        requested_exit = None
        exit_reason = None
        exit_index = None
        j = i + 1

        while j < len(candles):
            current_candle = candles[j]
            possible_exit, reason = _determine_exit(
                side=side,
                candle=current_candle,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            if possible_exit is not None:
                requested_exit = possible_exit
                exit_reason = reason
                exit_index = j
                break
            j += 1

        if requested_exit is None:
            exit_index = len(candles) - 1
            requested_exit = candles[exit_index].close
            exit_reason = "END_OF_DATA"

        # Gross P&L uses the common reference prices. Spread, slippage, and
        # commission are deducted below exactly once.
        gross_pnl = _calculate_trade_pnl(
            side=side,
            quantity=quantity,
            entry_price=requested_entry,
            exit_price=requested_exit,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
        )
        entry_spread_cost = _price_cost_to_account_currency(
            requested_price=requested_entry,
            executed_price=(
                requested_entry + abs(spread) / 2
                if side == "BUY"
                else requested_entry - abs(spread) / 2
            ),
            quantity=quantity,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
        )
        exit_spread_cost = _price_cost_to_account_currency(
            requested_price=requested_exit,
            executed_price=(
                requested_exit - abs(spread) / 2
                if side == "BUY"
                else requested_exit + abs(spread) / 2
            ),
            quantity=quantity,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
        )
        spread_cost = entry_spread_cost + exit_spread_cost

        entry_slippage_cost = _price_cost_to_account_currency(
            requested_price=requested_entry,
            executed_price=(
                requested_entry + abs(entry_slippage)
                if side == "BUY"
                else requested_entry - abs(entry_slippage)
            ),
            quantity=quantity,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
        )
        exit_slippage_cost = _price_cost_to_account_currency(
            requested_price=requested_exit,
            executed_price=(
                requested_exit - abs(exit_slippage)
                if side == "BUY"
                else requested_exit + abs(exit_slippage)
            ),
            quantity=quantity,
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
        )
        slippage_cost = entry_slippage_cost + exit_slippage_cost
        commission_cost = quantity * commission_per_unit
        execution_cost = spread_cost + slippage_cost + commission_cost
        net_pnl = gross_pnl - execution_cost
        equity += net_pnl

        holding_minutes = max(
            0.0,
            (candles[exit_index].timestamp - entry_candle.timestamp).total_seconds() / 60,
        )
        reward_risk_ratio = reward_distance / risk_distance

        trades.append(
            BacktestTrade(
                trade_number=len(trades) + 1,
                symbol=symbol,
                side=side,
                signal_time=signal_candle.timestamp,
                entry_time=entry_candle.timestamp,
                exit_time=candles[exit_index].timestamp,
                requested_entry=requested_entry,
                actual_entry=actual_entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quantity=quantity,
                gross_pnl=gross_pnl,
                execution_cost=execution_cost,
                net_pnl=net_pnl,
                exit_reason=exit_reason or "UNKNOWN",
                risk_amount=float(sizing.risk_amount),
                risk_percent=float(sizing.risk_percent),
                reward_risk_ratio=reward_risk_ratio,
                spread_cost=spread_cost,
                slippage_cost=slippage_cost,
                commission_cost=commission_cost,
                holding_minutes=holding_minutes,
            )
        )
        equity_curve.append(equity)
        i = exit_index + 1

    total_trades = len(trades)
    winning = [t for t in trades if t.net_pnl > 0]
    losing = [t for t in trades if t.net_pnl < 0]
    breakeven = [t for t in trades if t.net_pnl == 0]
    total_net_pnl = equity - initial_equity
    win_rate = len(winning) / total_trades * 100 if total_trades else 0.0
    average_trade = total_net_pnl / total_trades if total_trades else 0.0
    largest_win = max((t.net_pnl for t in trades), default=0.0)
    largest_loss = min((t.net_pnl for t in trades), default=0.0)

    peak = initial_equity
    max_drawdown_amount = 0.0
    max_drawdown_percent = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = peak - value
        drawdown_percent = drawdown / peak * 100 if peak > 0 else 0.0
        max_drawdown_amount = max(max_drawdown_amount, drawdown)
        max_drawdown_percent = max(max_drawdown_percent, drawdown_percent)

    gross_profit = sum(t.net_pnl for t in winning)
    gross_loss = abs(sum(t.net_pnl for t in losing))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )

    risk_units = [
        t.net_pnl / t.risk_amount
        for t in trades
        if t.risk_amount > 0
    ]
    expectancy_r = sum(risk_units) / len(risk_units) if risk_units else None

    side_performance = {}
    for side in ("BUY", "SELL"):
        side_trades = [t for t in trades if t.side == side]
        side_wins = sum(1 for t in side_trades if t.net_pnl > 0)
        side_performance[side] = {
            "trades": len(side_trades),
            "wins": side_wins,
            "losses": sum(1 for t in side_trades if t.net_pnl < 0),
            "win_rate_percent": (
                round(side_wins / len(side_trades) * 100, 2)
                if side_trades else 0.0
            ),
            "net_pnl": round(sum(t.net_pnl for t in side_trades), 2),
            "average_net_pnl": (
                round(sum(t.net_pnl for t in side_trades) / len(side_trades), 2)
                if side_trades else 0.0
            ),
        }

    monthly = defaultdict(list)
    for trade in trades:
        monthly[trade.exit_time.strftime("%Y-%m")].append(trade)

    monthly_performance = []
    for month in sorted(monthly):
        month_trades = monthly[month]
        month_wins = sum(1 for t in month_trades if t.net_pnl > 0)
        monthly_performance.append({
            "month": month,
            "trades": len(month_trades),
            "wins": month_wins,
            "losses": sum(1 for t in month_trades if t.net_pnl < 0),
            "win_rate_percent": round(month_wins / len(month_trades) * 100, 2),
            "net_pnl": round(sum(t.net_pnl for t in month_trades), 2),
            "execution_cost": round(sum(t.execution_cost for t in month_trades), 2),
        })

    return BacktestResult(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        initial_equity=initial_equity,
        final_equity=equity,
        total_trades=total_trades,
        winning_trades=len(winning),
        losing_trades=len(losing),
        breakeven_trades=len(breakeven),
        win_rate_percent=win_rate,
        total_net_pnl=total_net_pnl,
        average_trade_pnl=average_trade,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_drawdown_amount=max_drawdown_amount,
        max_drawdown_percent=max_drawdown_percent,
        profit_factor=profit_factor,
        trades=trades,
        total_spread_cost=sum(t.spread_cost for t in trades),
        total_slippage_cost=sum(t.slippage_cost for t in trades),
        total_commission_cost=sum(t.commission_cost for t in trades),
        total_execution_cost=sum(t.execution_cost for t in trades),
        expectancy_r=expectancy_r,
        max_consecutive_wins=_max_consecutive(trades, is_win=True),
        max_consecutive_losses=_max_consecutive(trades, is_win=False),
        average_holding_minutes=(
            sum(t.holding_minutes for t in trades) / total_trades
            if total_trades else 0.0
        ),
        side_performance=side_performance,
        monthly_performance=monthly_performance,
    )
