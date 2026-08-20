"""Trusted Python live-engine lifecycle and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX advisory locks; non-POSIX platforms skip single-instance guard.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

from quantforge.adapters import (
    CcxtExecutionAdapter,
    PollingBarFeed,
    SchwabExecutionAdapter,
)
from quantforge.adapters.ccxt import (
    CcxtConnector,
    fetch_warmup_bars,
    instrument_from_ccxt_market,
)
from quantforge.brokers.reconciliation import reconcile_schwab_account
from quantforge.brokers.schwab import SchwabConnector, credentials_for
from quantforge.domain.instruments import (
    AssetClass,
    Equity,
    InstrumentId,
)
from quantforge.execution import ExecutionService, PaperExecutionAdapter
from quantforge.live import PythonLiveEngine
from quantforge.live.engine import LiveQuote
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.control import GlobalRiskControl
from quantforge.risk.engine import DailyEntryCounter, RiskEngine, RiskLimits
from quantforge.strategy.bar import BarStrategy
from quantforge.strategy.registry import get_strategy

from apps.dashboard.backend.http_errors import sanitize_exception
from apps.dashboard.backend.jobs import _DEFAULT_SYMBOLS
from apps.dashboard.backend.routers.live import _find_perf_files, _load_perf

logger = logging.getLogger(__name__)
_engines: dict[str, dict[str, Any]] = {}
_PERSIST_FILE = Path.home() / ".quantforge" / "live" / "engines.json"
_restored = False

# Shared across all engines so the daily new-position limit is enforced
# globally and survives process restarts.
_DAILY_ENTRY_PATH = Path.home() / ".quantforge" / "risk" / "daily-entries.json"
_daily_entries = DailyEntryCounter(_DAILY_ENTRY_PATH)

# Watchdog: revive an engine whose loop exited silently (e.g. the feed hung
# and returned), with exponential backoff and a hard cap so a crash burst
# surfaces for manual intervention instead of restart-storming the venue.
_RESTART_BACKOFF_BASE_SECONDS = 5.0
_RESTART_BACKOFF_MAX_SECONDS = 60.0
_MAX_RESTART_ATTEMPTS = 3

#: Quotes older than this are treated as unavailable so risk quote-age/spread
#: checks never run against stale data (nor silently against made-up prices).
_QUOTE_MAX_AGE_SECONDS = 60.0

# Single-instance guard: a second dashboard process must not restore the same
# engines and double-submit orders.
_LOCK_PATH = Path.home() / ".quantforge" / "live" / "engines.lock"
_single_instance = True
if fcntl is not None:
    try:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _lock_fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _single_instance = False
        logger.error(
            "Another QuantForge dashboard holds the live-engine lock "
            "(%s) — refusing to run engines in this process",
            _LOCK_PATH,
        )


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

        def schwab_quote() -> LiveQuote | None:
            quote = connector.get_quote_bid_ask(symbol)
            if quote is None:
                return None
            when = quote["time"]
            if when is not None and (
                datetime.now(timezone.utc) - when
            ).total_seconds() > _QUOTE_MAX_AGE_SECONDS:
                # Stale (e.g. outside market hours): not a tradable quote.
                return None
            # ``when`` may be None (last/mark-only quote): pass it through as
            # None so the risk freshness gate rejects the order instead of a
            # fabricated timestamp. Never synthesize "now" for an unknown-age
            # quote.
            return LiveQuote(bid=quote["bid"], ask=quote["ask"], timestamp=when)

        feed = PollingBarFeed(
            lambda: connector.fetch_chart_bars(symbol, timeframe), poll_seconds=5
        )
        adapter = PaperExecutionAdapter() if demo else SchwabExecutionAdapter(connector)
        cash_currency = "USD"
    else:
        connector = CcxtConnector(venue, symbol, demo=demo)
        market = connector._exchange.market(symbol)
        # Never overwrite the venue's real maxLeverage with the requested
        # value: doing so makes the instrument-level leverage cap trivially
        # pass and a strategy can silently ask for more than the venue allows.
        # Use the venue's published cap when available; otherwise fall back to
        # the requested leverage (venue cap unknown) and let the operator's
        # risk_limits.max_leverage bound it.
        market_limits = market.get("limits") or {}
        venue_cap = (market_limits.get("leverage") or {}).get("max")
        market = {
            **market,
            "maxLeverage": float(venue_cap) if venue_cap else float(leverage),
        }
        instrument = instrument_from_ccxt_market(market, venue=venue)

        def ccxt_quote() -> LiveQuote | None:
            quote = connector.fetch_quote()
            if quote is None:
                return None
            when = quote["time"]
            if when is not None and (
                datetime.now(timezone.utc) - when
            ).total_seconds() > _QUOTE_MAX_AGE_SECONDS:
                return None
            # Same discipline as Schwab: an unknown-age quote carries
            # timestamp=None and trips the risk freshness gate rather than a
            # fabricated "now".
            return LiveQuote(bid=quote["bid"], ask=quote["ask"], timestamp=when)

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
    if not demo and venue == "schwab":
        ledger = reconcile_schwab_account(connector.get_account_snapshot())
    elif not demo:
        try:
            broker_position = connector.get_position()
        except Exception as exc:
            # A position query failure must not be treated as "flat" — that
            # could stack a duplicate position on top of an existing one.
            raise RuntimeError(
                f"cannot start live engine on {symbol}: "
                f"failed to read current position ({exc})"
            ) from exc
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
        risk=RiskEngine(
            limits,
            global_control=GlobalRiskControl(),
            daily_entries=_daily_entries,
        ),
        ledger=ledger,
        adapter=adapter,
    )
    quote_provider = schwab_quote if venue == "schwab" else ccxt_quote
    return PythonLiveEngine(
        strategy=strategy,
        instrument=instrument,
        execution=execution,
        position_size=position_size,
        leverage=leverage,
        feed=feed,
        warmup_bars=warmup_bars,
        quote_provider=quote_provider,
    )


async def restore_engines() -> int:
    global _restored
    if _restored:
        return 0
    _restored = True
    if not _single_instance:
        logger.error(
            "Live-engine lock is held by another process; not restoring engines"
        )
        return 0
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
    if not _single_instance:
        raise RuntimeError(
            "Another QuantForge dashboard process holds the live-engine lock; "
            "refusing to start a new engine here"
        )
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
        "restart_count": 0,
        "pending_restart": None,
    }
    _engines[engine_id] = entry

    _start_task(entry)
    _save_state()
    return engine_id


def _start_task(entry: dict[str, Any]) -> None:
    """Create the engine task, its done-callback and the warmup watcher."""
    engine_id = entry["engine_id"]
    engine = entry["engine"]

    async def run() -> None:
        try:
            await engine.start()
        except asyncio.CancelledError:
            await engine.stop()
            raise
        except Exception as exc:
            logger.exception("Live engine %s failed", engine_id)
            entry["status"] = "failed"
            entry["error"] = sanitize_exception(exc, prefix="live engine failed")
            _save_state()

    task = asyncio.create_task(run())
    entry["task"] = task

    def on_done(_task: asyncio.Task) -> None:
        # A live engine must never silently stop trading. Two regimes:
        #  * run() already diagnosed a failure (status == "failed") or the
        #    operator stopped it (status == "stopped"/"restarting") — do nothing.
        #  * the loop exited without any of the above (a silent/hung feed) —
        #    watchdog-restart it with backoff, capped, instead of leaving a
        #    stale row behind.
        if entry["status"] not in {"warmup", "running"}:
            return
        if _task.cancelled():
            entry["status"] = "stopped"
            _save_state()
            return
        entry["status"] = "restarting"
        entry["error"] = entry.get("error") or "engine loop exited unexpectedly"
        _save_state()
        logger.error(
            "Live engine %s exited unexpectedly — scheduling watchdog restart "
            "(attempt %s)",
            engine_id,
            entry["restart_count"] + 1,
        )
        entry["pending_restart"] = asyncio.create_task(_restart_engine(engine_id))

    task.add_done_callback(on_done)

    async def watch() -> None:
        while not engine._warmup_complete and not task.done():
            await asyncio.sleep(0.1)
        if not task.done() and entry["status"] in {"warmup", "restarting"}:
            entry["status"] = "running"
            entry["restart_count"] = 0  # a healthy run resets the crash budget
            _save_state()

    asyncio.create_task(watch())


async def _restart_engine(engine_id: str) -> None:
    """Watchdog: rebuild and restart a silently-died engine with backoff."""
    entry = _engines.get(engine_id)
    if entry is None or entry.get("status") != "restarting":
        return  # deleted or stopped while we were waiting
    if entry["restart_count"] >= _MAX_RESTART_ATTEMPTS:
        entry["status"] = "failed"
        entry["error"] = (
            entry.get("error") or ""
        ) + " — exceeded watchdog restart budget; manual intervention required"
        logger.error("Live engine %s exceeded watchdog restart budget", engine_id)
        _save_state()
        return
    delay = min(
        _RESTART_BACKOFF_BASE_SECONDS * (2 ** entry["restart_count"]),
        _RESTART_BACKOFF_MAX_SECONDS,
    )
    await asyncio.sleep(delay)
    if entry.get("status") != "restarting":
        return
    try:
        engine = _build_runtime(
            strategy_name=entry["strategy"],
            config_override=entry.get("config_override"),
            exchange=entry["exchange"],
            symbol=entry["symbol"],
            timeframe=entry["timeframe"],
            demo=entry["demo"],
            position_size=entry["position_size_usdt"],
            leverage=entry["leverage"],
            warmup_bars=entry["warmup_bars"],
            risk_limits=entry.get("risk_limits"),
        )
    except Exception as exc:
        entry["status"] = "failed"
        entry["error"] = sanitize_exception(exc, prefix="watchdog restart failed")
        logger.exception("Watchdog restart of %s failed during rebuild", engine_id)
        _save_state()
        return
    # Rebuild re-reads the live broker position, so restarting preserves order
    # safety: the ledger starts from what the venue actually holds.
    entry["engine"] = engine
    entry["task"] = None
    entry["status"] = "warmup"
    entry["error"] = None
    entry["restart_count"] += 1
    entry["pending_restart"] = None
    _start_task(entry)
    _save_state()
    logger.warning("Live engine %s restarted by watchdog (attempt %s)", engine_id, entry["restart_count"])


async def stop_engine(engine_id: str) -> None:
    entry = _engines.get(engine_id)
    if entry is None:
        raise KeyError(f"Engine {engine_id} not found")
    # Cancel a pending watchdog restart so it cannot revive the engine while
    # the operator is deliberately stopping it.
    pending = entry.get("pending_restart")
    if pending is not None and not pending.done():
        pending.cancel()
    entry["pending_restart"] = None
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
    if entry.get("status") in {"warmup", "running", "restarting"}:
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


async def emergency_halt_all(reason: str) -> dict[str, Any]:
    """Persist the master halt, cancel tracked orders, and stop all engines."""
    state = GlobalRiskControl().update(halted=True, reason=reason)
    canceled: list[str] = []
    errors: list[str] = []
    active_ids = [
        engine_id
        for engine_id, entry in _engines.items()
        if entry.get("status") in {"warmup", "running", "restarting"}
    ]
    for engine_id in active_ids:
        entry = _engines[engine_id]
        engine = entry.get("engine")
        adapter = getattr(getattr(engine, "execution", None), "adapter", None)
        connector = getattr(adapter, "connector", None)
        try:
            if connector is not None and hasattr(connector, "cancel_tracked_orders"):
                canceled.extend(connector.cancel_tracked_orders())
            elif connector is not None and hasattr(connector, "cancel_all_orders"):
                canceled.extend(
                    str(item.get("id") or item)
                    for item in connector.cancel_all_orders()
                )
        except Exception as exc:
            logger.exception("Emergency cancellation failed for %s", engine_id)
            errors.append(
                f"{engine_id}: {sanitize_exception(exc, prefix='cancel failed')}"
            )
        await stop_engine(engine_id)
    return {
        **asdict(state),
        "stopped_engines": active_ids,
        "canceled_orders": canceled,
        "errors": errors,
    }
