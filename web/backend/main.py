"""FastAPI application — QuantForge Web Backend."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.backend.routers import strategies, backtest, optimize, live, agent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: restore persisted live engines. Shutdown: save state."""
    from web.backend.live_engines import restore_engines, _save_state
    count = await restore_engines()
    if count:
        logger.info("Restored %d live engine(s) on startup", count)
    yield
    _save_state()


app = FastAPI(
    title="QuantForge API",
    description="Backtest, strategy configuration, and live monitoring API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(strategies.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(optimize.router, prefix="/api")
app.include_router(live.router, prefix="/api")
app.include_router(agent.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
