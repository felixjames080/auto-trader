from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.db.database import Base, engine

# Import models so SQLAlchemy registers their tables.
from app.models import trading  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Development database initialization.
    Base.metadata.create_all(bind=engine)

    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Secure automated trading platform. "
        "The current build operates in paper-trading mode."
    ),
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")


@app.get("/")
def root():
    return {
        "application": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "mode": "PAPER",
        "live_trading_enabled": False,
    }