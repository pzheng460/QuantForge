"""In-memory live engine manager.

Manages PineLiveEngine instances as asyncio tasks within the FastAPI
event loop.  Each engine is tracked in ``_engines`` with its config,
status, and asyncio task handle.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from quantforge.pine.live.engine import PineLiveEngine

from web.backend.jobs import _DEFAULT_SYMBOLS, _resolve_pine_source
from web.backend.routers.live import _find_perf_files, _load_perf

logger = logging.getLogger(__name__)

_engines: dict[str, dict[str, Any]] = {}


async def start_engine(
    strategy: str | None,
    pine_source: str | None,
    exchange: str,
    symbol: str | None,
    timeframe: str,
    demo: bool,
    position_size_usdt: float,
    leverage: int,
    warmup_bars: int,
    config_override: dict | None = None,
) -> str:
    """Create and start a PineLiveEngine as an asyncio task.

    Returns the engine_id (uuid).
    """
    source = _resolve_pine_source(strategy, pine_source, config_override)
    resolved_symbol = symbol or _DEFAULT_SYMBOLS.get(exchange, "BTC/USDT:USDT")
    strategy_name = strategy or "custom_strategy"

    engine = PineLiveEngine(
        pine_source=source,
        exchange=exchange,
        symbol=resolved_symbol,
        timeframe=timeframe,
        demo=demo,
        warmup_bars=warmup_bars,
        position_size_usdt=position_size_usdt,
        strategy_name=strategy_name,
        leverage=leverage,
    )

    engine_id = str(uuid.uuid4())[:8]

    _engines[engine_id] = {
        "engine_id": engine_id,
        "engine": engine,
        "task": None,
        "status": "warmup",
        "strategy": strategy_name,
        "exchange": exchange,
        "symbol": resolved_symbol,
        "timeframe": timeframe,
        "demo": demo,
        "leverage": leverage,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    async def _run() -> None:
        try:
            await engine.start()
        except asyncio.CancelledError:
            await engine.stop()
        except Exception as exc:
            logger.exception("Live engine %s failed", engine_id)
            if engine_id in _engines:
                _engines[engine_id]["status"] = "failed"
                _engines[engine_id]["error"] = str(exc)

    async def _watch_warmup() -> None:
        while not engine._warmup_complete and engine_id in _engines:
            await asyncio.sleep(1)
        if engine_id in _engines and _engines[engine_id]["status"] == "warmup":
            _engines[engine_id]["status"] = "running"

    loop = asyncio.get_running_loop()
    task = loop.create_task(_run())
    _engines[engine_id]["task"] = task
    loop.create_task(_watch_warmup())

    return engine_id


async def stop_engine(engine_id: str) -> None:
    """Stop a running engine gracefully."""
    entry = _engines.get(engine_id)
    if entry is None:
        raise KeyError(f"Engine {engine_id} not found")

    engine: PineLiveEngine = entry["engine"]
    task: asyncio.Task = entry["task"]

    await engine.stop()
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    entry["status"] = "stopped"


def list_engines() -> list[dict[str, Any]]:
    """Return all engines with their current performance data."""
    perf_files = _find_perf_files()
    result = []
    for eid, entry in _engines.items():
        perf = None
        strategy_name = entry["strategy"]
        if strategy_name in perf_files:
            perf = _load_perf(perf_files[strategy_name])
        result.append({
            "engine_id": eid,
            "status": entry["status"],
            "strategy": strategy_name,
            "exchange": entry["exchange"],
            "symbol": entry["symbol"],
            "timeframe": entry["timeframe"],
            "demo": entry["demo"],
            "leverage": entry["leverage"],
            "created_at": entry["created_at"],
            "error": entry["error"],
            "performance": perf,
        })
    return result


def get_engine(engine_id: str) -> dict[str, Any] | None:
    """Return a single engine entry or None."""
    return _engines.get(engine_id)
