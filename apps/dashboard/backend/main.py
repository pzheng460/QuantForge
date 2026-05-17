"""FastAPI application — QuantForge Web Backend."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.dashboard.backend.routers import strategies, backtest, optimize, live, agent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: restore persisted live engines. Shutdown: save state."""
    from apps.dashboard.backend.live_engines import restore_engines, _save_state
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


# ─── Production static serving ───────────────────────────────────────────────
# In dev, Vite serves the frontend on :5173 with HMR and the backend just
# exposes /api. In production (after `npm run build` populates
# apps/dashboard/frontend/dist/), this same FastAPI process also serves the
# built React app — no separate static server needed.
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="frontend-assets",
    )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # API and assets are handled above. Everything else is a SPA route
        # and should fall back to index.html so React Router can take over.
        index = _FRONTEND_DIST / "index.html"
        if not index.exists():
            return {"detail": "frontend not built"}
        return FileResponse(index)
    logger.info("Production static serving enabled from %s", _FRONTEND_DIST)
else:
    logger.info(
        "Dev mode: %s not found — frontend should be served by vite on :5173",
        _FRONTEND_DIST,
    )
