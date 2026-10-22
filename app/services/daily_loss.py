from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.trading import PaperPosition


def get_today_utc_start() -> datetime:
    now = datetime.now(timezone.utc)

    return now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def get_daily_realized_pnl(
    db: Session,
    paper_equity: float,
) -> dict:

    if paper_equity <= 0:
        raise ValueError(
            "Paper equity must be greater than zero."
        )

    today_start = get_today_utc_start()

    positions = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= today_start,
        )
        .all()
    )

    realized_pnl = sum(
        float(position.realized_pnl or 0)
        for position in positions
    )

    daily_loss_amount = max(
        0.0,
        -realized_pnl,
    )

    daily_loss_percent = (
        daily_loss_amount
        / paper_equity
    ) * 100

    return {
        "date": today_start.date().isoformat(),
        "closed_trades": len(positions),
        "realized_pnl": realized_pnl,
        "daily_loss_amount": daily_loss_amount,
        "daily_loss_percent": daily_loss_percent,
    }


def is_daily_loss_limit_reached(
    db: Session,
    paper_equity: float,
    max_daily_loss_percent: float,
) -> tuple[bool, dict]:

    daily = get_daily_realized_pnl(
        db=db,
        paper_equity=paper_equity,
    )

    locked = (
        daily["daily_loss_percent"]
        >= max_daily_loss_percent
    )

    return locked, daily