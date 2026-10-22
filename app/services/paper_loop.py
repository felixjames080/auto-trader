import threading
import time
from datetime import datetime, timezone

from app.adapters.mt5 import DevelopmentMarketDataAdapter
from app.db.database import SessionLocal
from app.models.trading import PaperPosition
from app.services.paper_monitor import monitor_paper_positions
from app.services.paper_worker import execute_and_persist_paper_cycle


_worker_thread = None
_stop_event = threading.Event()
_worker_lock = threading.Lock()


def _paper_loop(
    symbol: str,
    timeframe_minutes: int,
    lookback_days: int,
    paper_equity: float,
    interval_seconds: int,
):
    adapter = DevelopmentMarketDataAdapter()

    while not _stop_event.is_set():
        db = SessionLocal()

        try:
            symbol = symbol.upper()

            # -------------------------------------------------
            # 1. Get current paper market price
            # -------------------------------------------------
            quote = adapter.get_quote(symbol)

            current_price = quote.bid

            # -------------------------------------------------
            # 2. Monitor existing paper positions
            # -------------------------------------------------
            monitor_paper_positions(
                db=db,
                symbol=symbol,
                current_price=current_price,
            )

            # -------------------------------------------------
            # 3. Count open positions
            # -------------------------------------------------
            open_positions = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.symbol == symbol,
                    PaperPosition.status == "open",
                )
                .count()
            )

            # -------------------------------------------------
            # 4. Run a new autonomous market-analysis cycle
            # -------------------------------------------------
            if open_positions == 0:
                result = execute_and_persist_paper_cycle(
                    db=db,
                    symbol=symbol,
                    timeframe_minutes=timeframe_minutes,
                    lookback_days=lookback_days,
                    paper_equity=paper_equity,
                    max_open_trades=1,
                )

                print(
                    f"[PAPER LOOP] "
                    f"{datetime.now(timezone.utc).isoformat()} | "
                    f"{symbol} | "
                    f"{result.status} | "
                    f"{result.action}"
                )

            else:
                print(
                    f"[PAPER LOOP] "
                    f"{datetime.now(timezone.utc).isoformat()} | "
                    f"{symbol} | "
                    f"OPEN POSITION | "
                    f"price={current_price}"
                )

        except Exception as exc:
            print(
                f"[PAPER LOOP ERROR] "
                f"{datetime.now(timezone.utc).isoformat()} | "
                f"{type(exc).__name__}: {exc}"
            )

            # Fail closed:
            # an error must never cause a trade to be submitted.
            time.sleep(2)

        finally:
            db.close()

        _stop_event.wait(interval_seconds)


def start_paper_loop(
    symbol: str = "EURUSD",
    timeframe_minutes: int = 15,
    lookback_days: int = 30,
    paper_equity: float = 10_000.0,
    interval_seconds: int = 15,
):
    global _worker_thread

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return {
                "started": False,
                "status": "ALREADY_RUNNING",
            }

        _stop_event.clear()

        _worker_thread = threading.Thread(
            target=_paper_loop,
            kwargs={
                "symbol": symbol.upper(),
                "timeframe_minutes": timeframe_minutes,
                "lookback_days": lookback_days,
                "paper_equity": paper_equity,
                "interval_seconds": interval_seconds,
            },
            daemon=True,
        )

        _worker_thread.start()

        return {
            "started": True,
            "status": "RUNNING",
            "mode": "PAPER",
            "execution_allowed": False,
            "symbol": symbol.upper(),
            "interval_seconds": interval_seconds,
        }


def stop_paper_loop():
    global _worker_thread

    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            return {
                "stopped": False,
                "status": "NOT_RUNNING",
            }

        _stop_event.set()

        return {
            "stopped": True,
            "status": "STOPPING",
        }


def get_paper_loop_status():
    running = (
        _worker_thread is not None
        and _worker_thread.is_alive()
    )

    return {
        "mode": "PAPER",
        "running": running,
        "execution_allowed": False,
    }