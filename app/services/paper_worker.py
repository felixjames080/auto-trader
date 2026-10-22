from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from app.services.daily_loss import (
    get_daily_realized_pnl,
    is_daily_loss_limit_reached,
)
from uuid import uuid4
from app.services.instruments import get_instrument_spec
from app.services.execution_costs import (
    apply_entry_cost,
    calculate_execution_costs,
)
from sqlalchemy.orm import Session
from app.services.instruments import get_instrument_spec
from app.models.trading import AuditEvent, PaperOrder, PaperPosition
from app.adapters.mt5 import DevelopmentMarketDataAdapter
from app.services.market_data import validate_candles
from app.services.position_sizing import calculate_position_size
from app.services.risk import ProposedTrade, RiskSettings, validate_trade
from app.services.trade_decision import generate_trade_decision


@dataclass
class PaperCycleResult:
    status: str
    symbol: str
    action: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    position_size: float
    risk_amount: float
    risk_percent: float
    reward_risk_ratio: float | None
    reasons: list[str]
    timestamp: datetime
    execution_id: str | None = None
    position_id: str | None = None
    


def run_paper_cycle(
    symbol: str = "EURUSD",
    timeframe_minutes: int = 15,
    lookback_days: int = 30,
    paper_equity: float = 10_000.0,
    max_risk_percent: float = 1.0,
    max_position_size: float = 1.0,
    max_open_trades: int = 3,
    current_open_trades: int = 0,
    current_daily_loss_percent: float = 0.0,
    max_daily_loss_percent: float = 5.0,
):
    """
    Runs exactly ONE autonomous paper-trading decision cycle.

    Pipeline:

        market data
        -> data quality
        -> strategy
        -> market-derived SL/TP
        -> position sizing
        -> risk validation
        -> paper decision

    This function NEVER sends a real broker order.
    """

    timestamp = datetime.now(timezone.utc).isoformat()

    adapter = DevelopmentMarketDataAdapter()

    # ---------------------------------------------------------
    # 1. Check market-data adapter
    # ---------------------------------------------------------

    if not adapter.health_check():
        return PaperCycleResult(
            status="STOPPED",
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            position_size=0,
            risk_amount=0,
            risk_percent=0,
            reward_risk_ratio=None,
            reasons=["Market-data adapter health check failed."],
            timestamp=timestamp,
        )

    # ---------------------------------------------------------
    # 2. Get historical market data
    # ---------------------------------------------------------

    candles = adapter.get_historical_data(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
    )

    if not candles:
        return PaperCycleResult(
            status="STOPPED",
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            position_size=0,
            risk_amount=0,
            risk_percent=0,
            reward_risk_ratio=None,
            reasons=["No market data was returned."],
            timestamp=timestamp,
        )

    # ---------------------------------------------------------
    # 3. Validate market data
    # ---------------------------------------------------------

    quality = validate_candles(candles, timeframe_minutes)

    if not quality.healthy:
        return PaperCycleResult(
            status="STOPPED",
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            position_size=0,
            risk_amount=0,
            risk_percent=0,
            reward_risk_ratio=None,
            reasons=[
                "Market-data quality check failed.",
                *quality.errors,
            ],
            timestamp=timestamp,
        )

    # ---------------------------------------------------------
    # 4. Let the strategy analyze the market
    # ---------------------------------------------------------

    decision = generate_trade_decision(
        candles=candles,
        symbol=symbol,
    )

    # ---------------------------------------------------------
    # 5. If there is no valid setup, do nothing
    # ---------------------------------------------------------

    if decision.action == "NO_TRADE":
        return PaperCycleResult(
            status="WAITING",
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            position_size=0,
            risk_amount=0,
            risk_percent=0,
            reward_risk_ratio=None,
            reasons=decision.reasons,
            timestamp=timestamp,
        )

    # ---------------------------------------------------------
    # 6. Calculate position size from risk
    # ---------------------------------------------------------

    sizing = calculate_position_size(
        account_equity=paper_equity,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        max_risk_percent=max_risk_percent,
        max_position_size=max_position_size,
    )

    if not sizing.approved:
        return PaperCycleResult(
            status="REJECTED",
            symbol=symbol,
            action=decision.action,
            entry_price=decision.entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            position_size=sizing.quantity,
            risk_amount=sizing.risk_amount,
            risk_percent=sizing.risk_percent,
            reward_risk_ratio=decision.reward_risk_ratio,
            reasons=[
                *decision.reasons,
                f"Position sizing rejected: {sizing.reason}",
            ],
            timestamp=timestamp,
        )

    # ---------------------------------------------------------
    # 7. Build proposed trade
    # ---------------------------------------------------------

    trade = ProposedTrade(
        symbol=symbol,
        side=decision.action,
        quantity=sizing.quantity,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
    )

    # ---------------------------------------------------------
    # 8. Apply risk engine
    # ---------------------------------------------------------

    risk_settings = RiskSettings(
        account_equity=paper_equity,
        max_position_size=max_position_size,
        max_open_trades=max_open_trades,
        current_open_trades=current_open_trades,
        max_risk_per_trade_percent=max_risk_percent,
        current_daily_loss_percent=current_daily_loss_percent,
        max_daily_loss_percent=max_daily_loss_percent,
        trading_paused=False,
    )

    instrument = get_instrument_spec(result.symbol)

    if instrument is None:
      return PaperCycleResult(
        status="REJECTED",
        symbol=result.symbol,
        action=result.action,
        entry_price=result.entry_price,
        stop_loss=result.stop_loss,
        take_profit=result.take_profit,
        position_size=sizing.quantity,
        risk_amount=0,
        risk_percent=0,
        reward_risk_ratio=decision.reward_risk_ratio,
        reasons=[
            *decision.reasons,
            f"No instrument specification exists for {result.symbol}.",
            "Trade rejected because monetary risk cannot be verified safely.",
        ],
        timestamp=timestamp,
    )

    risk_result = validate_trade(
    trade=trade,
    settings=risk_settings,
    instrument=instrument,
)

    # ---------------------------------------------------------
    # 9. Everything passed
    # ---------------------------------------------------------

    return PaperCycleResult(
        status="APPROVED_PAPER_TRADE",
        symbol=symbol,
        action=decision.action,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        position_size=sizing.quantity,
        risk_amount=risk_result.estimated_risk_amount,
        risk_percent=risk_result.estimated_risk_percent,
        reward_risk_ratio=risk_result.reward_risk_ratio,
        reasons=[
            *decision.reasons,
            "Market data passed quality checks.",
            "Position sizing passed.",
            "All configured risk checks passed.",
            "Trade is approved for PAPER mode only.",
        ],
        timestamp=timestamp,
    )

def execute_and_persist_paper_cycle(
    db: Session,
    symbol: str = "EURUSD",
    timeframe_minutes: int = 15,
    lookback_days: int = 30,
    paper_equity: float = 10_000.0,
    max_risk_percent: float = 1.0,
    max_position_size: float = 1.0,
    max_open_trades: int = 3,
    current_daily_loss_percent: float = 0.0,
    max_daily_loss_percent: float = 5.0,
):
    """
    Runs one autonomous paper cycle and, if approved,
    persists the paper order and position.

    NEVER sends a real broker order.
    """

        # ---------------------------------------------------------
    # 0. Check automatic daily-loss lockout
    # ---------------------------------------------------------

    daily_loss_locked, daily_loss = is_daily_loss_limit_reached(
        db=db,
        paper_equity=paper_equity,
        max_daily_loss_percent=max_daily_loss_percent,
    )

    if daily_loss_locked:

        audit = AuditEvent(
            event_type="PAPER_TRADING_DAILY_LOSS_LOCK",
            outcome="blocked",
            description=(
                f"Paper trading blocked because the daily loss "
                f"limit was reached. "
                f"RealizedPnL={daily_loss['realized_pnl']}, "
                f"DailyLossPercent="
                f"{daily_loss['daily_loss_percent']:.4f}%, "
                f"Limit={max_daily_loss_percent:.4f}%."
            ),
        )

        db.add(audit)
        db.commit()

        return PaperCycleResult(
            status="TRADING_LOCKED",
            symbol=symbol,
            action="NO_TRADE",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            position_size=0,
            risk_amount=0,
            risk_percent=0,
            reward_risk_ratio=None,
            reasons=[
                "Automatic daily-loss protection is active.",
                (
                    f"Today's realized loss is "
                    f"{daily_loss['daily_loss_percent']:.2f}%."
                ),
                (
                    f"Maximum allowed daily loss is "
                    f"{max_daily_loss_percent:.2f}%."
                ),
            ],
            timestamp=datetime.now(timezone.utc),
        )

    # ---------------------------------------------------------
    # 1. Run market analysis + risk checks
    # ---------------------------------------------------------

    result = run_paper_cycle(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
        paper_equity=paper_equity,
        max_risk_percent=max_risk_percent,
        max_position_size=max_position_size,
        max_open_trades=max_open_trades,
        current_daily_loss_percent=current_daily_loss_percent,
        max_daily_loss_percent=max_daily_loss_percent,
    )

    # ---------------------------------------------------------
    # 2. Nothing to execute
    # ---------------------------------------------------------

    if result.status != "APPROVED_PAPER_TRADE":
        return result

    # ---------------------------------------------------------
    # 3. Prevent duplicate open positions
    # ---------------------------------------------------------

    existing_position = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.symbol == result.symbol,
            PaperPosition.status == "open",
        )
        .first()
    )

    if existing_position:
        result.status = "REJECTED"
        result.reasons.append(
            "An open paper position already exists for this symbol."
        )

        db.add(
            AuditEvent(
                event_type="PAPER_TRADE_REJECTED_DUPLICATE",
                outcome="rejected",
                description=(
                    f"Duplicate paper trade blocked for {result.symbol}. "
                    f"Existing position: {existing_position.position_id}"
                ),
            )
        )

        db.commit()

        return result

       # ---------------------------------------------------------
    # 4. Generate unique IDs
    # ---------------------------------------------------------

    execution_id = f"paper-exec-{uuid4()}"
    position_id = f"paper-pos-{uuid4()}"

    # ---------------------------------------------------------
    # 5. Apply simulated execution costs
    # ---------------------------------------------------------

    # DEVELOPMENT ONLY.
    # Real broker values must come from the connected platform.
    paper_spread = 0.00010
    paper_entry_slippage = 0.00002
    paper_exit_slippage = 0.00002
    paper_commission_per_unit = 0.0

    actual_entry_price = apply_entry_cost(
        side=result.action,
        requested_price=result.entry_price,
        spread=paper_spread,
        slippage=paper_entry_slippage,
    )

    execution_costs = calculate_execution_costs(
        quantity=result.position_size,
        spread=paper_spread,
        entry_slippage=paper_entry_slippage,
        exit_slippage=paper_exit_slippage,
        commission_per_unit=paper_commission_per_unit,
    )

    # ---------------------------------------------------------
    # 6. Create paper order
    # ---------------------------------------------------------

    order = PaperOrder(
        execution_id=execution_id,
        symbol=result.symbol,
        side=result.action,
        quantity=Decimal(str(result.position_size)),
        entry_price=Decimal(str(actual_entry_price)),
        stop_loss=Decimal(str(result.stop_loss)),
        take_profit=Decimal(str(result.take_profit)),
        status="filled",
    )

    # ---------------------------------------------------------
    # 7. Create paper position
    # ---------------------------------------------------------

    position = PaperPosition(
        position_id=position_id,
        execution_id=execution_id,
        symbol=result.symbol,
        side=result.action,
        quantity=Decimal(str(result.position_size)),
        entry_price=Decimal(str(actual_entry_price)),
        current_price=Decimal(str(actual_entry_price)),
        stop_loss=Decimal(str(result.stop_loss)),
        take_profit=Decimal(str(result.take_profit)),
        status="open",
        realized_pnl=Decimal("0"),
    )

    # ---------------------------------------------------------
    # 8. Audit event
    # ---------------------------------------------------------

    audit = AuditEvent(
        event_type="PAPER_TRADE_OPENED",
        outcome="success",
        description=(
            f"Paper {result.action} opened for {result.symbol}. "
            f"RequestedEntry={result.entry_price}, "
            f"ActualEntry={actual_entry_price}, "
            f"SL={result.stop_loss}, "
            f"TP={result.take_profit}, "
            f"Size={result.position_size}, "
            f"Risk={result.risk_percent:.4f}%, "
            f"EstimatedExecutionCosts={execution_costs.total_cost}"
        ),
    )

    # ---------------------------------------------------------
    # 9. Persist everything together
    # ---------------------------------------------------------

    try:
        db.add(order)
        db.add(position)
        db.add(audit)

        db.commit()

        db.refresh(order)
        db.refresh(position)

    except Exception:
        db.rollback()
        raise

    # ---------------------------------------------------------
    # 10. Attach execution IDs to result
    # ---------------------------------------------------------

    result.execution_id = execution_id
    result.position_id = position_id

    return result