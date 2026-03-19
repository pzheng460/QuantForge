"""Live engine manager with file-based persistence.

Manages PineLiveEngine instances as asyncio tasks within the FastAPI
event loop.  Each engine is tracked in ``_engines`` with its config,
status, and asyncio task handle.

Engine configs are persisted to ``~/.quantforge/live/engines.json`` so
that running engines automatically restart after uvicorn reload / restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantforge.pine.live.engine import PineLiveEngine

from web.backend.jobs import _DEFAULT_SYMBOLS, _resolve_pine_source
from web.backend.routers.live import _find_perf_files, _load_perf

logger = logging.getLogger(__name__)

_engines: dict[str, dict[str, Any]] = {}
_PERSIST_FILE = Path.home() / ".quantforge" / "live" / "engines.json"
_restored = False  # guard against double-restore


# ─── Persistence ──────────────────────────────────────────────────────────────


def _save_state() -> None:
    """Persist all non-stopped engine configs to disk."""
    configs = []
    for eid, entry in _engines.items():
        if entry["status"] in ("stopped", "failed"):
            continue
        configs.append({
            "engine_id": eid,
            "strategy": entry["strategy"],
            "pine_source": entry.get("pine_source"),
            "exchange": entry["exchange"],
            "symbol": entry["symbol"],
            "timeframe": entry["timeframe"],
            "demo": entry["demo"],
            "leverage": entry["leverage"],
            "position_size_usdt": entry.get("position_size_usdt", 100.0),
            "warmup_bars": entry.get("warmup_bars", 500),
            "created_at": entry["created_at"],
        })
    _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERSIST_FILE.write_text(json.dumps(configs, indent=2))
    logger.info("Persisted %d engine configs to %s", len(configs), _PERSIST_FILE)


def _load_state() -> list[dict]:
    """Load persisted engine configs from disk."""
    if not _PERSIST_FILE.exists():
        return []
    try:
        return json.loads(_PERSIST_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load engine state: %s", e)
        return []


async def restore_engines() -> int:
    """Restore persisted engines after uvicorn reload. Returns count restored."""
    global _restored
    if _restored:
        return 0
    _restored = True

    configs = _load_state()
    if not configs:
        return 0

    count = 0
    for cfg in configs:
        try:
            # Skip if already running (shouldn't happen, but be safe)
            for entry in _engines.values():
                if (
                    entry["strategy"] == cfg["strategy"]
                    and entry["status"] in ("warmup", "running")
                ):
                    logger.info("Engine for %s already running, skipping restore", cfg["strategy"])
                    break
            else:
                eid = await start_engine(
                    strategy=cfg["strategy"],
                    pine_source=cfg.get("pine_source"),
                    exchange=cfg["exchange"],
                    symbol=cfg["symbol"],
                    timeframe=cfg["timeframe"],
                    demo=cfg["demo"],
                    position_size_usdt=cfg.get("position_size_usdt", 100.0),
                    leverage=cfg["leverage"],
                    warmup_bars=cfg.get("warmup_bars", 500),
                    _engine_id=cfg.get("engine_id"),
                )
                logger.info("Restored engine %s for %s", eid, cfg["strategy"])
                count += 1
        except Exception:
            logger.exception("Failed to restore engine for %s", cfg["strategy"])

    return count


# ─── Engine Lifecycle ─────────────────────────────────────────────────────────


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
    _engine_id: str | None = None,
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

    engine_id = _engine_id or str(uuid.uuid4())[:8]

    _engines[engine_id] = {
        "engine_id": engine_id,
        "engine": engine,
        "task": None,
        "status": "warmup",
        "strategy": strategy_name,
        "pine_source": pine_source,
        "exchange": exchange,
        "symbol": resolved_symbol,
        "timeframe": timeframe,
        "demo": demo,
        "leverage": leverage,
        "position_size_usdt": position_size_usdt,
        "warmup_bars": warmup_bars,
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
                _save_state()

    async def _watch_warmup() -> None:
        while not engine._warmup_complete and engine_id in _engines:
            await asyncio.sleep(1)
        if engine_id in _engines and _engines[engine_id]["status"] == "warmup":
            _engines[engine_id]["status"] = "running"

    loop = asyncio.get_running_loop()
    task = loop.create_task(_run())
    _engines[engine_id]["task"] = task
    loop.create_task(_watch_warmup())

    _save_state()
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
    _save_state()


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
