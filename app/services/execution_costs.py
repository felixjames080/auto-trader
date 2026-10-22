from dataclasses import dataclass


@dataclass
class ExecutionCosts:
    spread_cost: float
    entry_slippage_cost: float
    exit_slippage_cost: float
    commission: float
    total_cost: float


def calculate_execution_costs(
    quantity: float,
    spread: float,
    entry_slippage: float,
    exit_slippage: float,
    commission_per_unit: float = 0.0,
) -> ExecutionCosts:
    """
    Development/paper-trading execution-cost model.

    This does NOT connect to a broker.
    Broker-specific spread, slippage and commission rules
    must replace these assumptions before live trading.
    """

    quantity = abs(quantity)

    spread_cost = quantity * abs(spread)

    entry_slippage_cost = quantity * abs(entry_slippage)

    exit_slippage_cost = quantity * abs(exit_slippage)

    commission = quantity * abs(commission_per_unit)

    total_cost = (
        spread_cost
        + entry_slippage_cost
        + exit_slippage_cost
        + commission
    )

    return ExecutionCosts(
        spread_cost=spread_cost,
        entry_slippage_cost=entry_slippage_cost,
        exit_slippage_cost=exit_slippage_cost,
        commission=commission,
        total_cost=total_cost,
    )


def apply_entry_cost(
    side: str,
    requested_price: float,
    spread: float,
    slippage: float,
) -> float:
    """
    Simulates the price actually received after spread
    and entry slippage.
    """

    side = side.upper()

    if requested_price <= 0:
        raise ValueError("Requested price must be greater than zero.")

    spread = abs(spread)
    slippage = abs(slippage)

    if side == "BUY":
        return requested_price + (spread / 2) + slippage

    if side == "SELL":
        return requested_price - (spread / 2) - slippage

    raise ValueError("Side must be BUY or SELL.")


def apply_exit_cost(
    side: str,
    requested_price: float,
    spread: float,
    slippage: float,
) -> float:
    """
    Simulates the price actually received when closing
    a paper position.
    """

    side = side.upper()

    if requested_price <= 0:
        raise ValueError("Requested price must be greater than zero.")

    spread = abs(spread)
    slippage = abs(slippage)

    if side == "BUY":
        return requested_price - (spread / 2) - slippage

    if side == "SELL":
        return requested_price + (spread / 2) + slippage

    raise ValueError("Side must be BUY or SELL.")


def calculate_net_pnl(
    side: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    tick_size: float = 0.00001,
    tick_value: float = 1.0,
    total_execution_cost: float = 0.0,
) -> tuple[float, float]:
    """
    Calculate monetary P&L using instrument tick specifications.

    DEVELOPMENT/PAPER TRADING:
    The tick_size and tick_value must come from the instrument
    specification. Before live trading, these values must come
    from the connected broker/platform.

    Example:
        EURUSD
        tick_size  = 0.00001
        tick_value = 1.00

    A price movement of 0.00100 therefore represents:
        0.00100 / 0.00001 = 100 ticks
    """

    side = side.upper()
    quantity = abs(quantity)

    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    if entry_price <= 0:
        raise ValueError("Entry price must be greater than zero.")

    if exit_price <= 0:
        raise ValueError("Exit price must be greater than zero.")

    if tick_size <= 0:
        raise ValueError("Tick size must be greater than zero.")

    if tick_value <= 0:
        raise ValueError("Tick value must be greater than zero.")

    if side == "BUY":
        price_difference = exit_price - entry_price

    elif side == "SELL":
        price_difference = entry_price - exit_price

    else:
        raise ValueError("Side must be BUY or SELL.")

    # Convert price movement into ticks.
    ticks_moved = price_difference / tick_size

    # Convert ticks into monetary P&L.
    gross_pnl = ticks_moved * tick_value * quantity

    net_pnl = gross_pnl - abs(total_execution_cost)

    return gross_pnl, net_pnl
   