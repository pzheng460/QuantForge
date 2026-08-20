"""FastAPI application — QuantForge Web Backend."""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.dashboard.backend.auth import api_key_configured, request_api_key_authorized

# ─── Logging: capture INFO from quantforge.* loggers ─────────────────────────
# Keep live-engine and broker lifecycle logs visible in the service console.
# calls (warmup progress, order submission, leverage set, etc.) get dropped
# silently because uvicorn only configures its own logger. In live trading
# this means we have ZERO observability into what the engine is doing.
_root = logging.getLogger()
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
    for h in _root.handlers
):
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    _root.addHandler(_h)
_root.setLevel(logging.INFO)
# Quiet down very-chatty libraries so the engine log stays readable.
for noisy in ("ccxt.base.exchange", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Logging must be configured before router imports, hence the late import.
from apps.dashboard.backend.routers import (  # noqa: E402
    strategies,
    backtest,
    optimize,
    live,
    brokers,
    risk_options,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: restore persisted live engines. Shutdown: save state."""
    from apps.dashboard.backend.live_engines import restore_engines, _save_state

    running_under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if running_under_pytest:
        logger.info("Skipping live engine restore under pytest")
    else:
        count = await restore_engines()
        if count:
            logger.info("Restored %d live engine(s) on startup", count)
    yield
    if not running_under_pytest:
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
app.include_router(brokers.router, prefix="/api")
app.include_router(risk_options.router, prefix="/api")

# ─── Optional API-key authentication ────────────────────────────────────────
# The dashboard binds to 127.0.0.1 by default (see apps/dashboard/start.sh).
# When an operator deliberately exposes it to the network (--host 0.0.0.0),
# they must set QUANTFORGE_API_KEY; every /api* request (except /api/health)
# then requires the `X-API-Key` header, otherwise trading controls on this
# backend are reachable by anyone on the network.
#
# Starlette's HTTP middleware does NOT run for WebSocket upgrades, so the WS
# handlers enforce the same key themselves (see auth.py).
if api_key_configured():

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        if request.url.path.startswith("/api") and request.url.path != "/api/health":
            if not request_api_key_authorized(request):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "invalid or missing API key"},
                )
        return await call_next(request)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def unknown_api(path: str):
    """Keep unknown API requests from falling through to the SPA."""
    raise HTTPException(status_code=404, detail="API route not found")


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
