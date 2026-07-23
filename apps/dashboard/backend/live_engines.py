"""Trusted Python live-engine lifecycle and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantforge.adapters import (
    CcxtExecutionAdapter,
    PollingBarFeed,
    SchwabExecutionAdapter,
)
from quantforge.adapters.ccxt import CcxtConnector, fetch_warmup_bars
from quantforge.brokers.schwab import SchwabConnector, credentials_for
from quantforge.domain.instruments import (
    AssetClass,
    CryptoDerivative,
    CryptoSpot,
    Equity,
    InstrumentId,
)
from quantforge.execution import ExecutionService, PaperExecutionAdapter
from quantforge.live import PythonLiveEngine
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.engine import RiskEngine, RiskLimits
from quantforge.strategy.bar import BarStrategy
from quantforge.strategy.registry import get_strategy

from apps.dashboard.backend.jobs import _DEFAULT_SYMBOLS
from apps.dashboard.backend.routers.live import _find_perf_files, _load_perf

logger = logging.getLogger(__name__)
_engines: dict[str, dict[str, Any]] = {}
_PERSIST_FILE = Path.home() / ".quantforge" / "live" / "engines.json"
_restored = False


def _save_state() -> None:
    fields = (
        "engine_id",
        "strategy",
        "exchange",
        "symbol",
        "timeframe",
        "demo",
        "leverage",
        "position_size_usdt",
        "warmup_bars",
        "config_override",
        "risk_limits",
        "created_at",
        "status",
        "stopped_at",
        "error",
    )
    payload = [
        {field: entry.get(field) for field in fields}
        for entry in _engines.values()
    ]
    _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PERSIST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(_PERSIST_FILE)


def _load_state() -> list[dict]:
    if not _PERSIST_FILE.exists():
        return []
    try:
        return json.loads(_PERSIST_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to load persisted live engines")
        return []


def _schwab_connector(symbol: str) -> SchwabConnector:
    config_path = Path.home() / ".quantforge/schwab/config.json"
    try:
        account_hash = json.loads(config_path.read_text()).get("account_hash")
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        account_hash = None
    return SchwabConnector(
        credentials_for("trading"),
        market_credentials=credentials_for("market_data"),
        account_hash=account_hash,
        symbol=symbol,
    )


def _build_runtime(
    *,
    strategy_name: str,
    config_override: dict | None,
    exchange: str,
    symbol: str,
    timeframe: str,
    demo: bool,
    position_size: float,
    leverage: float,
    warmup_bars: int,
    risk_limits: dict | None,
) -> PythonLiveEngine:
    import quantforge.strategies  # noqa: F401

    strategy_cls = get_strategy(strategy_name)
    strategy = strategy_cls(strategy_cls.config_model(**(config_override or {})))
    if not isinstance(strategy, BarStrategy):
        raise ValueError(
            f"{strategy_name} is event-driven and cannot use the bar live engine"
        )

    venue = exchange.lower()
    if venue == "schwab":
        instrument = Equity(
            id=InstrumentId(symbol.upper(), AssetClass.EQUITY, "schwab")
        )
        connector = _schwab_connector(symbol)
        feed = PollingBarFeed(
            lambda: connector.fetch_chart_bars(symbol, timeframe), poll_seconds=5
        )
        adapter = PaperExecutionAdapter() if demo else SchwabExecutionAdapter(connector)
        cash_currency = "USD"
    else:
        is_derivative = ":" in symbol
        asset_class = (
            AssetClass.CRYPTO_PERPETUAL
            if is_derivative
            else AssetClass.CRYPTO_SPOT
        )
        instrument = (
            CryptoDerivative(
                id=InstrumentId(symbol, asset_class, venue),
                max_leverage=leverage,
            )
            if is_derivative
            else CryptoSpot(id=InstrumentId(symbol, asset_class, venue))
        )
        connector = CcxtConnector(venue, symbol, demo=demo)

        def load_rows() -> list[list]:
            return [
                [b.timestamp, b.open, b.high, b.low, b.close, b.volume]
                for b in fetch_warmup_bars(symbol, venue, timeframe, warmup_bars)
            ]

        feed = PollingBarFeed(load_rows, poll_seconds=5)
        adapter = CcxtExecutionAdapter(connector)
        cash_currency = "USDT"

    ledger = PortfolioLedger(
        cash={cash_currency: 0 if venue == "schwab" and not demo else 1_000_000}
    )
    if not demo:
        broker_position = connector.get_position()
        if broker_position:
            quantity = float(broker_position["contracts"])
            if str(broker_position.get("side", "")).lower() == "short":
                quantity = -quantity
            ledger.positions[instrument.id] = Position(
                instrument=instrument,
                quantity=quantity,
                average_price=float(broker_position.get("entryPrice") or 0),
            )
    limits = RiskLimits(
        live_enabled=True,
        max_order_notional=float((risk_limits or {}).get("max_order_notional", 10_000)),
        max_spread_pct=float((risk_limits or {}).get("max_spread_pct", 0.15)),
        max_leverage=float((risk_limits or {}).get("max_leverage", 3)),
        max_daily_new_positions=int(
            (risk_limits or {}).get("max_daily_new_positions", 10)
        ),
        require_fresh_quote=True,
    )
    execution = ExecutionService(
        risk=RiskEngine(limits), ledger=ledger, adapter=adapter
    )
    return PythonLiveEngine(
        strategy=strategy,
        instrument=instrument,
        execution=execution,
        position_size=position_size,
        leverage=leverage,
        feed=feed,
        warmup_bars=warmup_bars,
    )


async def restore_engines() -> int:
    global _restored
    if _restored:
        return 0
    _restored = True
    count = 0
    for cfg in _load_state():
        if cfg.get("status") in {"stopped", "failed"}:
            eid = cfg.get("engine_id") or str(uuid.uuid4())[:8]
            _engines[eid] = {**cfg, "engine": None, "task": None}
            continue
        try:
            await start_engine(
                strategy=cfg["strategy"],
                exchange=cfg["exchange"],
                symbol=cfg["symbol"],
                timeframe=cfg["timeframe"],
                demo=cfg["demo"],
                position_size_usdt=cfg["position_size_usdt"],
                leverage=cfg["leverage"],
                warmup_bars=cfg["warmup_bars"],
                config_override=cfg.get("config_override"),
                risk_limits=cfg.get("risk_limits"),
                _engine_id=cfg.get("engine_id"),
            )
            count += 1
        except Exception:
            logger.exception("Failed to restore %s", cfg.get("strategy"))
    return count


async def start_engine(
    *,
    strategy: str,
    exchange: str,
    symbol: str | None,
    timeframe: str,
    demo: bool,
    position_size_usdt: float,
    leverage: int,
    warmup_bars: int,
    config_override: dict | None = None,
    risk_limits: dict | None = None,
    _engine_id: str | None = None,
) -> str:
    resolved_symbol = symbol or _DEFAULT_SYMBOLS.get(exchange, "BTC/USDT:USDT")
    engine = _build_runtime(
        strategy_name=strategy,
        config_override=config_override,
        exchange=exchange,
        symbol=resolved_symbol,
        timeframe=timeframe,
        demo=demo,
        position_size=position_size_usdt,
        leverage=leverage,
        warmup_bars=warmup_bars,
        risk_limits=risk_limits,
    )
    engine_id = _engine_id or str(uuid.uuid4())[:8]
    entry = {
        "engine_id": engine_id,
        "engine": engine,
        "task": None,
        "status": "warmup",
        "strategy": strategy,
        "exchange": exchange,
        "symbol": resolved_symbol,
        "timeframe": timeframe,
        "demo": demo,
        "leverage": leverage,
        "position_size_usdt": position_size_usdt,
        "warmup_bars": warmup_bars,
        "config_override": config_override,
        "risk_limits": risk_limits or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    _engines[engine_id] = entry

    async def run() -> None:
        try:
            await engine.start()
        except asyncio.CancelledError:
            await engine.stop()
            raise
        except Exception as exc:
            logger.exception("Live engine %s failed", engine_id)
            entry["status"] = "failed"
            entry["error"] = str(exc)
            _save_state()

    task = asyncio.create_task(run())
    entry["task"] = task

    async def watch() -> None:
        while not engine._warmup_complete and not task.done():
            await asyncio.sleep(0.1)
        if not task.done() and entry["status"] == "warmup":
            entry["status"] = "running"
            _save_state()

    asyncio.create_task(watch())
    _save_state()
    return engine_id


async def stop_engine(engine_id: str) -> None:
    entry = _engines.get(engine_id)
    if entry is None:
        raise KeyError(f"Engine {engine_id} not found")
    engine = entry.get("engine")
    task = entry.get("task")
    if engine is not None:
        await engine.stop()
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    entry["status"] = "stopped"
    entry["stopped_at"] = datetime.now(timezone.utc).isoformat()
    _save_state()


def delete_engine(engine_id: str) -> None:
    entry = _engines.get(engine_id)
    if entry is None:
        raise KeyError(f"Engine {engine_id} not found")
    if entry.get("status") in {"warmup", "running"}:
        raise ValueError("engine is still active; stop it before deleting it")
    del _engines[engine_id]
    _save_state()


def list_engines() -> list[dict[str, Any]]:
    perf_files = _find_perf_files()
    result = []
    for eid, entry in _engines.items():
        result.append(
            {
                "engine_id": eid,
                "status": entry["status"],
                "strategy": entry["strategy"],
                "exchange": entry["exchange"],
                "symbol": entry["symbol"],
                "timeframe": entry["timeframe"],
                "demo": entry["demo"],
                "leverage": entry["leverage"],
                "created_at": entry["created_at"],
                "stopped_at": entry.get("stopped_at"),
                "error": entry.get("error"),
                "performance": (
                    _load_perf(perf_files[entry["strategy"]])
                    if entry["strategy"] in perf_files
                    else None
                ),
            }
        )
    return result


def get_engine(engine_id: str) -> dict[str, Any] | None:
    return _engines.get(engine_id)
