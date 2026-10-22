from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.services.daily_loss import (
    get_daily_realized_pnl,
)
from app.services.trade_decision import generate_trade_decision
from app.core.config import settings
from app.services.paper_worker import run_paper_cycle
from app.db.database import get_db
from app.services.paper_monitor import monitor_paper_positions
from app.services.paper_worker import execute_and_persist_paper_cycle
from pydantic import BaseModel
from app.services.backtesting import run_backtest
from app.adapters.mt5 import DevelopmentMarketDataAdapter
from app.services.position_sizing import calculate_position_size
from app.models import AuditEvent, PaperOrder, PaperPosition
from app.services.market_data import (
    generate_demo_candles,
    validate_candles,
)
from app.services.paper_trading import (
    execute_paper_trade,
)
from app.services.risk import (
    ProposedTrade,
    RiskSettings,
    validate_trade,
)

from app.services.paper_loop import (
    start_paper_loop,
    stop_paper_loop,
    get_paper_loop_status,
)

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.paper_test import open_controlled_paper_test
from app.strategies.ema_rsi import generate_signal


router = APIRouter()


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "application": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "live_trading_enabled": settings.live_trading_enabled,
    }


@router.get("/system/status")
def system_status():
    return {
        "mode": "PAPER",
        "live_trading": False,
        "message": "Paper trading is enabled. Live trading is disabled.",
    }


@router.get("/market-data")
def market_data(
    symbol: str = Query(
        default="EURUSD",
        min_length=3,
        max_length=20,
    ),
    timeframe_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
    ),
    lookback_days: int = Query(
        default=30,
        ge=1,
        le=365,
    ),
):
    """
    Development-only market data endpoint.

    The returned data is synthetic and must not be used for live trading.
    """

    candles = generate_demo_candles(
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
    )

    quality = validate_candles(
        candles=candles,
        timeframe_minutes=timeframe_minutes,
    )

    if not quality.healthy:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Market data failed quality checks.",
                "errors": quality.errors,
            },
        )

    return {
        "symbol": symbol.upper(),
        "timeframe_minutes": timeframe_minutes,
        "lookback_days": lookback_days,
        "source": "synthetic-development-data",
        "live_trading_allowed": False,
        "data_quality": {
            "healthy": quality.healthy,
            "errors": quality.errors,
            "warnings": quality.warnings,
        },
        "candle_count": len(candles),
        "candles": [
            {
                "timestamp": candle.timestamp.isoformat(),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ],
    }



@router.post("/paper/test/open")
def open_paper_test_trade(
    symbol: str = "EURUSD",
    account_equity: float = 10000.0,
    max_risk_percent: float = 1.0,
    db: Session = Depends(get_db),
):
    """
    Explicit PAPER-ONLY lifecycle test.

    This endpoint never sends a live order.
    """

    return open_controlled_paper_test(
        db=db,
        symbol=symbol,
        account_equity=account_equity,
        max_risk_percent=max_risk_percent,
    )

@router.get("/strategy/signal")
def strategy_signal(
    symbol: str = Query(
        default="EURUSD",
        min_length=3,
        max_length=20,
    ),
    timeframe_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
    ),
    lookback_days: int = Query(
        default=30,
        ge=1,
        le=365,
    ),
):
    """
    Generate a paper-only strategy signal.

    This endpoint analyzes synthetic development data.
    It does not place orders.
    """

    candles = generate_demo_candles(
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
    )

    quality = validate_candles(
        candles=candles,
        timeframe_minutes=timeframe_minutes,
    )

    if not quality.healthy:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Trading blocked because "
                    "market data failed validation."
                ),
                "errors": quality.errors,
            },
        )

    signal = generate_signal(candles)

    return {
        "symbol": symbol.upper(),
        "timeframe_minutes": timeframe_minutes,
        "lookback_days": lookback_days,
        "strategy": "EMA20/EMA50 + RSI14",
        "mode": "PAPER",
        "execution_allowed": False,
        "signal": signal.direction,
        "reasons": signal.reason,
        "indicators": signal.indicators,
        "candle_count": signal.candle_count,
        "data_quality": {
            "healthy": quality.healthy,
            "errors": quality.errors,
            "warnings": quality.warnings,
        },
    }



@router.get("/trade/plan")
def trade_plan(
    symbol: str = Query(
        default="EURUSD",
        min_length=3,
        max_length=20,
    ),
    timeframe_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
    ),
    lookback_days: int = Query(
        default=30,
        ge=1,
        le=365,
    ),
    account_equity: float = Query(
        default=10000.0,
        gt=0,
    ),
    max_risk_percent: float = Query(
        default=1.0,
        gt=0,
    ),
    max_position_size: float = Query(
        default=1.0,
        gt=0,
    ),
):
    """
    Generate a complete autonomous paper-trade plan.

    This endpoint does NOT execute a trade.

    The bot:
    1. Validates market data.
    2. Analyzes the market.
    3. Decides BUY, SELL, or NO_TRADE.
    4. Determines entry, SL and TP.
    5. Calculates position size from risk.
    """

    candles = generate_demo_candles(
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
    )

    quality = validate_candles(
        candles=candles,
        timeframe_minutes=timeframe_minutes,
    )

    if not quality.healthy:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Trade plan blocked because "
                    "market data failed validation."
                ),
                "errors": quality.errors,
            },
        )

    decision = generate_trade_decision(
        candles=candles,
        symbol=symbol.upper(),
    )

    if decision.action == "NO_TRADE":
        return {
            "mode": "PAPER",
            "execution_allowed": False,
            "action": "NO_TRADE",
            "symbol": decision.symbol,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "position_size": 0,
            "risk_amount": 0,
            "risk_percent": 0,
            "reward_risk_ratio": decision.reward_risk_ratio,
            "reasons": decision.reasons,
        }

    sizing = calculate_position_size(
        account_equity=account_equity,
        entry_price=decision.entry_price,
        stop_loss=decision.stop_loss,
        max_risk_percent=max_risk_percent,
        max_position_size=max_position_size,
    )

    if not sizing.approved:
        return {
            "mode": "PAPER",
            "execution_allowed": False,
            "action": "NO_TRADE",
            "symbol": decision.symbol,
            "entry_price": decision.entry_price,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
            "position_size": 0,
            "risk_amount": 0,
            "risk_percent": 0,
            "reward_risk_ratio": decision.reward_risk_ratio,
            "reasons": [
                *decision.reasons,
                sizing.reason,
            ],
        }

    return {
        "mode": "PAPER",
        "execution_allowed": False,
        "action": decision.action,
        "symbol": decision.symbol,
        "timeframe_minutes": timeframe_minutes,
        "lookback_days": lookback_days,
        "entry_price": decision.entry_price,
        "stop_loss": decision.stop_loss,
        "take_profit": decision.take_profit,
        "position_size": sizing.quantity,
        "risk_amount": sizing.risk_amount,
        "risk_percent": sizing.risk_percent,
        "reward_risk_ratio": decision.reward_risk_ratio,
        "reasons": decision.reasons,
        "position_sizing_reason": sizing.reason,
    }




@router.post("/risk/check")
def risk_check(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    account_equity: float = 10000.0,
    max_position_size: float = 1.0,
    max_open_trades: int = 3,
    current_open_trades: int = 0,
    max_risk_per_trade_percent: float = 1.0,
    current_daily_loss_percent: float = 0.0,
    max_daily_loss_percent: float = 3.0,
    trading_paused: bool = False,
):
    """
    Validate a proposed paper trade against risk controls.

    This endpoint never places an order.
    """

    trade = ProposedTrade(
        symbol=symbol.upper(),
        side=side.upper(),
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    risk_settings = RiskSettings(
        account_equity=account_equity,
        max_position_size=max_position_size,
        max_open_trades=max_open_trades,
        current_open_trades=current_open_trades,
        max_risk_per_trade_percent=max_risk_per_trade_percent,
        current_daily_loss_percent=current_daily_loss_percent,
        max_daily_loss_percent=max_daily_loss_percent,
        trading_paused=trading_paused,
    )

    result = validate_trade(
        trade=trade,
        settings=risk_settings,
    )

    return {
        "symbol": trade.symbol,
        "side": trade.side,
        "mode": "PAPER",
        "execution_allowed": False,
        "approved": result.approved,
        "reasons": result.reasons,
        "estimated_risk_amount": result.estimated_risk_amount,
        "estimated_risk_percent": result.estimated_risk_percent,
        "reward_risk_ratio": result.reward_risk_ratio,
    }

    
@router.post("/paper/cycle/execute")
def execute_paper_cycle(
    symbol: str = Query("EURUSD"),
    timeframe_minutes: int = Query(15, gt=0),
    lookback_days: int = Query(30, gt=0),
    paper_equity: float = Query(10000, gt=0),
    db: Session = Depends(get_db),
):
    result = execute_and_persist_paper_cycle(
        db=db,
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
        paper_equity=paper_equity,
    )

    return {
        "mode": "PAPER",
        "execution_allowed": False,
        "status": result.status,
        "symbol": result.symbol,
        "action": result.action,
        "entry_price": result.entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "position_size": result.position_size,
        "risk_amount": result.risk_amount,
        "risk_percent": result.risk_percent,
        "reward_risk_ratio": result.reward_risk_ratio,
        "execution_id": getattr(result, "execution_id", None),
        "position_id": getattr(result, "position_id", None),
        "reasons": result.reasons,
        "timestamp": result.timestamp,
    }


@router.get("/trade/decision")
def trade_decision(
    symbol: str = Query(
        default="EURUSD",
        min_length=3,
        max_length=20,
    ),
    timeframe_minutes: int = Query(
        default=15,
        ge=1,
        le=1440,
    ),
    lookback_days: int = Query(
        default=30,
        ge=1,
        le=365,
    ),
):
    """
    Generate an autonomous paper-trading decision.

    This endpoint analyzes development market data and determines:
    BUY, SELL, or NO_TRADE.

    It does NOT execute an order.
    """

    candles = generate_demo_candles(
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
    )

    quality = validate_candles(
        candles=candles,
        timeframe_minutes=timeframe_minutes,
    )

    if not quality.healthy:
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Trading decision blocked because "
                    "market data failed validation."
                ),
                "errors": quality.errors,
            },
        )

    decision = generate_trade_decision(
        candles=candles,
        symbol=symbol.upper(),
    )

    return {
        "mode": "PAPER",
        "execution_allowed": False,
        "symbol": decision.symbol,
        "timeframe_minutes": timeframe_minutes,
        "lookback_days": lookback_days,
        "action": decision.action,
        "entry_price": decision.entry_price,
        "stop_loss": decision.stop_loss,
        "take_profit": decision.take_profit,
        "risk_distance": decision.risk_distance,
        "reward_distance": decision.reward_distance,
        "reward_risk_ratio": decision.reward_risk_ratio,
        "reasons": decision.reasons,
        "data_quality": {
            "healthy": quality.healthy,
            "errors": quality.errors,
            "warnings": quality.warnings,
        },

        
    }


@router.get("/paper/cycle")
def paper_cycle(
    symbol: str = Query("EURUSD"),
    timeframe_minutes: int = Query(15, ge=1),
    lookback_days: int = Query(30, ge=1),
    paper_equity: float = Query(10_000.0, gt=0),
):
    result = run_paper_cycle(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
        paper_equity=paper_equity,
    )

    return {
        "mode": "PAPER",
        "execution_allowed": False,
        "status": result.status,
        "symbol": result.symbol,
        "action": result.action,
        "entry_price": result.entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "position_size": result.position_size,
        "risk_amount": result.risk_amount,
        "risk_percent": result.risk_percent,
        "reward_risk_ratio": result.reward_risk_ratio,
        "reasons": result.reasons,
        "timestamp": result.timestamp,
    }

@router.get("/paper/daily-loss")
def get_paper_daily_loss(
    paper_equity: float = 10000.0,
    db: Session = Depends(get_db),
):
    return {
        "mode": "PAPER",
        **get_daily_realized_pnl(
            db=db,
            paper_equity=paper_equity,
        ),
    }    

@router.post("/backtest/run")
def run_backtest_endpoint(
    symbol: str = "EURUSD",
    timeframe_minutes: int = 15,
    lookback_days: int = 30,
    initial_equity: float = 10_000.0,
):
    from app.adapters.mt5 import (
    DevelopmentMarketDataAdapter,
    MT5MarketDataAdapter,
)
from app.services.backtesting import run_backtest

source = data_source.strip().lower()

if source == "demo":
    adapter = DevelopmentMarketDataAdapter()

elif source == "mt5":
    adapter = MT5MarketDataAdapter()

else:
    raise HTTPException(
        status_code=400,
        detail="Invalid data_source. Use 'demo' or 'mt5'.",
    )

candles = adapter.get_historical_data(
    symbol=symbol,
    timeframe_minutes=timeframe_minutes,
    lookback_days=lookback_days,
)

if not candles:
    raise HTTPException(
        status_code=503,
        detail=f"No historical candles returned from data source '{source}'.",
    )

result = run_backtest(
    candles=candles,
    symbol=symbol,
    initial_equity=initial_equity,
)

trades = result.trades

total_trades = len(trades)

    winning_trades = [
            trade for trade in trades
            if float(trade.net_pnl) > 0
        ]

    losing_trades = [
            trade for trade in trades
            if float(trade.net_pnl) < 0
        ]


    breakeven_trades = [
            trade for trade in trades
            if float(trade.net_pnl) == 0
        ]

    total_net_pnl = sum(
            float(trade.net_pnl)
            for trade in trades
        )

    average_trade_pnl = (
            total_net_pnl / total_trades
            if total_trades
            else 0
        )

    win_rate = (
            (len(winning_trades) / total_trades) * 100
            if total_trades
            else 0
        )

    gross_profit = sum(
            float(trade.net_pnl)
            for trade in winning_trades
        )

    gross_loss = abs(
            sum(
                float(trade.net_pnl)
                for trade in losing_trades
            )
        )

    profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else None
        )

    largest_win = max(
            (float(trade.net_pnl) for trade in trades),
            default=0,
        )

    largest_loss = min(
            (float(trade.net_pnl) for trade in trades),
            default=0,
        )

    final_equity = initial_equity + total_net_pnl

    return {
            "mode": "BACKTEST",

            "market": {
                "symbol": symbol,
                "timeframe_minutes": timeframe_minutes,
                "lookback_days": lookback_days,
                "candles_analyzed": len(candles),
            },

            "equity": {
                "initial": round(initial_equity, 2),
                "final": round(final_equity, 2),
                "net_pnl": round(total_net_pnl, 2),
                "return_percent": round(
                    (total_net_pnl / initial_equity) * 100,
                    2,
                ),
            },

            "performance": {
                "total_trades": total_trades,
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "breakeven_trades": len(breakeven_trades),
                "win_rate_percent": round(win_rate, 2),
                "average_trade_pnl": round(average_trade_pnl, 2),
                "largest_win": round(largest_win, 2),
                "largest_loss": round(largest_loss, 2),
                "profit_factor": (
                    round(profit_factor, 2)
                    if profit_factor is not None
                    else None
                ),
            },

            "risk": {
                "max_drawdown_amount": round(
                    float(result.max_drawdown_amount),
                    2,
                ),
                "max_drawdown_percent": round(
                    float(result.max_drawdown_percent),
                    2,
                ),
            },

            "execution_costs": {
        "total_spread_cost": round(result.total_spread_cost, 2),
        "total_slippage_cost": round(result.total_slippage_cost, 2),
        "total_commission_cost": round(result.total_commission_cost, 2),
        "total_execution_cost": round(result.total_execution_cost, 2),
    },

    "risk": {
        "max_drawdown_amount": round(
            float(result.max_drawdown_amount), 2
        ),
        "max_drawdown_percent": round(
            float(result.max_drawdown_percent), 2
        ),
        "expectancy_r": (
            round(result.expectancy_r, 4)
            if result.expectancy_r is not None
            else None
        ),
        "max_consecutive_wins": result.max_consecutive_wins,
        "max_consecutive_losses": result.max_consecutive_losses,
    },

    "trading_behavior": {
        "average_holding_minutes": round(
            result.average_holding_minutes, 2
        ),
        "side_performance": result.side_performance,
    },

    "monthly_performance": result.monthly_performance,

            "strategy": {
                "average_reward_risk": round(
                    sum(
                        float(trade.reward_risk_ratio)
                        for trade in trades
                        if trade.reward_risk_ratio is not None
                    )
                    / max(
                        1,
                        sum(
                            1
                            for trade in trades
                            if trade.reward_risk_ratio is not None
                        ),
                    ),
                    2,
                ),
            },

            "note": (
                "This is a development backtest using the current "
                "development market-data adapter. It is not evidence "
                "of live profitability."
            ),
        }; UnboundLocalError



@router.post("/paper/positions/monitor")
def monitor_paper_positions_endpoint(
    symbol: str = Query(...),
    current_price: float = Query(..., gt=0),
    db: Session = Depends(get_db),
):
    """
    Update paper positions and trigger SL/TP closures.

    PAPER TRADING ONLY.
    """

    results = monitor_paper_positions(
        db=db,
        symbol=symbol,
        current_price=current_price,
    )

    return {
        "mode": "PAPER",
        "symbol": symbol.upper(),
        "current_price": current_price,
        "positions_checked": len(results),
        "positions": results,
    }


@router.get("/paper/positions")
def get_paper_positions(
    symbol: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(PaperPosition)

    if symbol:
        query = query.filter(
            PaperPosition.symbol == symbol.upper()
        )

    if status:
        query = query.filter(
            PaperPosition.status == status.lower()
        )

    positions = query.order_by(
        PaperPosition.opened_at.desc()
    ).all()

    return {
        "mode": "PAPER",
        "count": len(positions),
        "positions": [
            {
                "position_id": position.position_id,
                "execution_id": position.execution_id,
                "symbol": position.symbol,
                "side": position.side,
                "quantity": float(position.quantity),
                "entry_price": float(position.entry_price),
                "current_price": float(position.current_price),
                "stop_loss": float(position.stop_loss),
                "take_profit": float(position.take_profit),
                "status": position.status,
                "realized_pnl": float(position.realized_pnl),
                "opened_at": position.opened_at,
                "closed_at": position.closed_at,
                "close_reason": position.close_reason,
            }
            for position in positions
        ],
    }


@router.post("/paper/auto-cycle")
def run_automatic_paper_cycle(
    symbol: str = Query("EURUSD"),
    timeframe_minutes: int = Query(15, gt=0),
    lookback_days: int = Query(30, gt=0),
    paper_equity: float = Query(10000, gt=0),
    db: Session = Depends(get_db),
):
    """
    Run one complete autonomous paper-trading cycle.

    The bot:
    1. Gets market data.
    2. Validates data quality.
    3. Generates a trade decision.
    4. Calculates position size.
    5. Runs risk checks.
    6. Opens a paper position only if approved.
    """

    result = execute_and_persist_paper_cycle(
        db=db,
        symbol=symbol.upper(),
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
        paper_equity=paper_equity,
    )

    return {
        "mode": "PAPER",
        "execution_allowed": False,
        "status": result.status,
        "symbol": result.symbol,
        "action": result.action,
        "entry_price": result.entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "position_size": result.position_size,
        "risk_amount": result.risk_amount,
        "risk_percent": result.risk_percent,
        "reward_risk_ratio": result.reward_risk_ratio,
        "execution_id": getattr(result, "execution_id", None),
        "position_id": getattr(result, "position_id", None),
        "reasons": result.reasons,
        "timestamp": result.timestamp,
    }


@router.post("/paper/loop/start")
def start_automatic_paper_loop(
    symbol: str = Query("EURUSD"),
    timeframe_minutes: int = Query(15, gt=0),
    lookback_days: int = Query(30, gt=0),
    paper_equity: float = Query(10000, gt=0),
    interval_seconds: int = Query(15, ge=5),
):
    return start_paper_loop(
        symbol=symbol,
        timeframe_minutes=timeframe_minutes,
        lookback_days=lookback_days,
        paper_equity=paper_equity,
        interval_seconds=interval_seconds,
    )


@router.post("/paper/loop/stop")
def stop_automatic_paper_loop():
    return stop_paper_loop()


@router.get("/paper/loop/status")
def paper_loop_status():
    return get_paper_loop_status()    

class RiskCheckRequest(BaseModel):
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    account_equity: float = 10000.0
    max_position_size: float = 1.0
    max_open_trades: int = 3
    current_open_trades: int = 0
    max_risk_per_trade_percent: float = 1.0
    current_daily_loss_percent: float = 0.0
    max_daily_loss_percent: float = 3.0
    trading_paused: bool = False

@router.post("/paper/execute")
def execute_paper_order(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    account_equity: float = 10000.0,
    max_position_size: float = 1.0,
    max_open_trades: int = 3,
    current_open_trades: int = 0,
    max_risk_per_trade_percent: float = 1.0,
    current_daily_loss_percent: float = 0.0,
    max_daily_loss_percent: float = 3.0,
    trading_paused: bool = False,
    db: Session = Depends(get_db),
):
    """
    Execute a paper trade.

    This endpoint NEVER sends an order to a broker.
    """

    trade = ProposedTrade(
        symbol=symbol.upper(),
        side=side.upper(),
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )

    risk_settings = RiskSettings(
        account_equity=account_equity,
        max_position_size=max_position_size,
        max_open_trades=max_open_trades,
        current_open_trades=current_open_trades,
        max_risk_per_trade_percent=max_risk_per_trade_percent,
        current_daily_loss_percent=current_daily_loss_percent,
        max_daily_loss_percent=max_daily_loss_percent,
        trading_paused=trading_paused,
    )

    result = execute_paper_trade(
        trade=trade,
        risk_settings=risk_settings,
    )

    audit = AuditEvent(
        event_type="paper_trade_execution",
        outcome="approved" if result.approved else "rejected",
        description=(
            f"Paper trade {result.status}: "
            f"{trade.side} {trade.quantity} {trade.symbol} "
            f"at {trade.entry_price}. "
            f"Reasons: {' | '.join(result.reasons)}"
        ),
    )

    db.add(audit)

    if not result.approved:
        db.commit()

        return {
            "approved": False,
            "mode": "PAPER",
            "execution_id": result.execution_id,
            "position_id": None,
            "status": "rejected",
            "reasons": result.reasons,
        }

    order = PaperOrder(
        execution_id=result.execution_id,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        status="filled",
    )

    position = PaperPosition(
        position_id=result.position_id,
        execution_id=result.execution_id,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        current_price=trade.entry_price,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
        status="open",
        realized_pnl=0,
    )

    db.add(order)
    db.add(position)

    db.commit()

    return {
        "approved": True,
        "mode": "PAPER",
        "execution_id": result.execution_id,
        "position_id": result.position_id,
        "status": "open",
        "symbol": trade.symbol,
        "side": trade.side,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "stop_loss": trade.stop_loss,
        "take_profit": trade.take_profit,
        "reasons": result.reasons,
    }

    

    