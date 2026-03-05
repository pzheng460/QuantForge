"""FastAPI application — NexusTrader Web Backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.backend.routers import strategies, backtest, optimize, live

app = FastAPI(
    title="NexusTrader API",
    description="Backtest, strategy configuration, and live monitoring API",
    version="1.0.0",
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


@app.get("/api/health")
async def health():
    return {"status": "ok"}
