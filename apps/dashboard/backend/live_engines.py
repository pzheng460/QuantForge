"""Trusted Python live-engine lifecycle and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
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

logger = logging.getLogger(__name__)
_engines: dict[str, dict[str, Any]] = {}
# Serializes registry read-modify-write (duplicate check + insert, delete) so
# two concurrent /live/start requests cannot both pass the "no engine running"
# check and double-start the same strategy.
_registry_lock = threading.Lock()
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
        "trades",
        "status",
        "stopped_at",
        "error",
    )
    # Snapshot the registry under the lock: start_engine/delete_engine mutate
    # _engines under _registry_lock, and get_live_engines/list_engines run
    # sync on the FastAPI threadpool, so an unguarded iteration can raise
    # "dictionary changed size during iteration". _save_state is never called
    # from inside _registry_lock (callers release it first), so a plain Lock
    # cannot self-deadlock here.
    with _registry_lock:
        payload = []
        for entry in _engines.values():
            # Fold any orders the engine submitted since the last save into the
            # persisted trade list before writing.
            _sync_trades(entry)
            payload.append({field: entry.get(field) for field in fields})
    _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = _PERSIST_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, _PERSIST_FILE)
    # Same discipline as DailyEntryCounter/OptionReportStore: atomic write +
    # 0600 so the persisted engine state is never world/group-readable.
    _PERSIST_FILE.chmod(0o600)


def _load_state() -> list[dict]:
    if not _PERSIST_FILE.exists():
        return []
    try:
        return json.loads(_PERSIST_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to load persisted live engines")
        return []


def _sync_trades(entry: dict[str, Any]) -> None:
    """Fold orders the engine submitted since the last sync into ``entry["trades"]``.

    The engine appends to its own in-memory ``order_log``; this merges those
    records into the registry entry (deduped by broker order id) so they are
    both visible via ``list_engines`` and persisted by ``_save_state``.
    """
    engine = entry.get("engine")
    new_records = list(getattr(engine, "order_log", ())) if engine is not None else []
    if not new_records:
        return
    trades = entry.setdefault("trades", [])
    seen = {t.get("order_id") for t in trades}
    for rec in new_records:
        if not rec.get("order_id") or rec["order_id"] in seen:
            continue
        merged = dict(rec)
        merged.setdefault("engine_id", entry["engine_id"])
        trades.append(merged)
        seen.add(rec["order_id"])
    trades.sort(key=lambda t: t.get("time", ""), reverse=True)


def _live_position_snapshot(
    entry: dict[str, Any],
) -> tuple[dict | None, float | None]:
    """Best-effort live position + last price for a running engine.

    Reads straight from the broker adapter (Bitget UTA position endpoint for
    crypto) so the dashboard shows REAL holdings, not a reconstructed guess.
    Never raises: any adapter/broker failure degrades to ``(None, None)`` and
    the engine status/error fields carry the authoritative health signal.
    """
    engine = entry.get("engine")
    adapter = getattr(getattr(engine, "execution", None), "adapter", None)
    # get_position/fetch_quote live on the underlying connector
    # (CcxtConnector for crypto; the adapter wrapper only exposes submit).
    connector = getattr(adapter, "connector", None) or adapter
    if connector is None:
        return None, None
    position: dict | None = None
    last_price: float | None = None
    try:
        raw = connector.get_position()
        if raw:
            danger = float(raw.get("contracts") or 0)
            if danger > 0:
                position = {
                    "side": raw.get("side"),
                    "quantity": danger,
                    # Bitget UTA reports the real entry in ``avgPrice``.
                    "entry_price": (
                        float(raw["entryPrice"]) if raw.get("entryPrice") else None
                    ),
                    "unrealized_pnl": raw.get("unrealizedPnl"),
                    "mark_price": raw.get("markPrice"),
                    "profit_rate": raw.get("profitRate"),
                }
    except Exception as exc:  # noqa: BLE001 — snapshot must never fail the list
        logger.warning("Position snapshot failed for %s: %s", entry["engine_id"], exc)
    try:
        quote = connector.fetch_quote()
        if quote:
            bid, ask = (quote or {}).get("bid"), (quote or {}).get("ask")
            if bid and ask:
                last_price = (float(bid) + float(ask)) / 2
            elif bid:
                last_price = float(bid)
            elif ask:
                last_price = float(ask)
    except Exception as exc:  # noqa: BLE001 — price is best-effort
        logger.warning("Last-price snapshot failed for %s: %s", entry["engine_id"], exc)
    return position, last_price


def _account_positions(connector: Any) -> list[dict]:
    """Account-scoped open positions (shared by every engine on the account).

    Returns the same snapshot shape as ``_live_position_snapshot`` but as a
    list, so the UI renders holdings once at account level instead of
    implying each engine owns them.
    """
    try:
        raw = connector.get_position()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("Account positions snapshot failed: %s", exc)
        return []
    if not raw:
        return []
    return [
        {
            "symbol": getattr(connector, "symbol", None),
            "side": raw.get("side"),
            "quantity": raw.get("contracts"),
            "entry_price": (
                float(raw["entryPrice"]) if raw.get("entryPrice") else None
            ),
            "unrealized_pnl": raw.get("unrealizedPnl"),
            "mark_price": raw.get("markPrice"),
            "profit_rate": raw.get("profitRate"),
        }
    ]


def _account_snapshot() -> dict | None:
    """Best-effort account-level summary from the first live crypto connector.

    Uses the Bitget UTA account-assets endpoint so equity/unrealised PnL are
    real broker numbers, not a sum of reconstructed trades. Returns None when
    no active engine/adapter can provide it (e.g. Schwab-only session).
    """
    for entry in _engines.values():
        if entry.get("status") not in {"warmup", "running", "restarting"}:
            continue
        engine = entry.get("engine")
        adapter = getattr(getattr(engine, "execution", None), "adapter", None)
        connector = getattr(adapter, "connector", None) or adapter
        if connector is None:
            continue
        # Only crypto connectors expose the UTA account-assets endpoint.
        exchange_id = getattr(connector, "exchange_id", None)
        if exchange_id != "bitget":
            continue
        try:
            resp = connector._exchange.privateUtaGetV3AccountAssets(
                {"category": "USDT-FUTURES"}
            )
            data = (resp or {}).get("data") or {}
            usdt_asset = next(
                (a for a in (data.get("assets") or []) if a.get("coin") == "USDT"),
                {},
            )
            def _f(key: str) -> float | None:
                v = data.get(key) or usdt_asset.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            return {
                "equity": _f("usdtEquity"),
                "available": _f("available"),
                "unrealized_pnl": _f("usdtUnrealisedPnl")
                or _f("unrealisedPnl"),
                "position_value": _f("positionValue"),
                "positions": _account_positions(connector),
            }
        except Exception as exc:  # noqa: BLE001 — summary must never crash the list
            logger.warning("Account snapshot failed for %s: %s", entry["engine_id"], exc)
            return None
    return None


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


def _net_position_from_trades(trades: list[dict] | None) -> tuple[float, float] | None:
    """Net position this engine OWNS, from its OWN recorded submissions.

    Every BUY adds and every SELL subtracts. Returns ``(net_quantity,
    avg_price)`` where ``avg_price`` is the volume-weighted average of the
    side that nets positive (so it is always a meaningful entry reference).
    Returns ``None`` when the engine's own history nets to zero.

    This is the ownership basis for multi-engine shared-account isolation: an
    engine must never claim holdings that another engine or a manual operator
    placed on the shared venue account.
    """
    buy_qty = buy_cost = sell_qty = sell_notional = 0.0
    for tr in trades or []:
        try:
            side = str(tr.get("side", "")).lower()
            q = float(tr.get("quantity") or 0)
            p = float(tr.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if side == "buy":
            buy_qty += q
            buy_cost += q * p
        elif side == "sell":
            sell_qty += q
            sell_notional += q * p
    net = buy_qty - sell_qty
    if abs(net) < 1e-12:
        return None
    if net > 0:
        avg = buy_cost / buy_qty if buy_qty else 0.0
    else:
        avg = sell_notional / sell_qty if sell_qty else 0.0
    return net, avg


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
    trades: list[dict] | None = None,
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
        # Multi-engine shared-account isolation: an engine owns ONLY the
        # position implied by its OWN recorded submissions (trades). It must
        # not claim holdings that other engines or manual operators placed on
        # the shared venue account — otherwise one strategy could reduce-only
        # close another engine's position on a hedge/U account.
        net_position = _net_position_from_trades(trades)
        if net_position is not None:
            ledger.positions[instrument.id] = Position(
                instrument=instrument,
                quantity=net_position[0],
                average_price=net_position[1],
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
                created_at=cfg.get("created_at"),
                trades=cfg.get("trades"),
            )
            count += 1
        except Exception:
            logger.exception("Failed to restore %s", cfg.get("strategy"))
    return count


class EngineAlreadyRunningError(RuntimeError):
    """Raised by start_engine when the strategy already has an active engine.

    The bindings map this to HTTP 409; start_live's pre-check is only an
    early UX shortcut — this is the authoritative, race-free gate.
    """


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
    created_at: str | None = None,
    trades: list[dict] | None = None,
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
        trades=trades,
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
        # Keep the engine's original creation time when restoring (so "started"
        # reflects the original launch, not each backend restart); otherwise
        # stamp now.
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        # Persisted order history (restored on restart); live submissions are
        # folded in via _sync_trades so trades survive backend restarts.
        "trades": list(trades or []),
        "error": None,
        "restart_count": 0,
        "pending_restart": None,
    }
    # Authoritative duplicate gate: check-and-insert is atomic under the
    # registry lock, so the friendly router pre-check can never be raced.
    with _registry_lock:
        for existing in _engines.values():
            if (
                existing["strategy"] == strategy
                and existing["status"] in {"warmup", "running", "restarting"}
            ):
                raise EngineAlreadyRunningError(
                    f"Engine for '{strategy}' is already {existing['status']}"
                )
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
        # Fold this lifecycle's new submissions into the persisted trade list
        # so the rebuilt engine owns exactly what it has actually traded.
        _sync_trades(entry)
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
            trades=entry.get("trades"),
        )
    except Exception as exc:
        entry["status"] = "failed"
        entry["error"] = sanitize_exception(exc, prefix="watchdog restart failed")
        logger.exception("Watchdog restart of %s failed during rebuild", engine_id)
        _save_state()
        return
    # Rebuild reconstructs the engine's OWN position from its trade history,
    # so restarting preserves per-engine ownership (never another engine's).
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
    with _registry_lock:
        entry = _engines.get(engine_id)
        if entry is None:
            raise KeyError(f"Engine {engine_id} not found")
        if entry.get("status") in {"warmup", "running", "restarting"}:
            raise ValueError("engine is still active; stop it before deleting it")
        del _engines[engine_id]
    _save_state()


def _owned_position(entry: dict[str, Any]) -> dict | None:
    """The position THIS engine believes it owns (from its own ledger).

    Distinct from ``position`` (account-scoped, shared across engines): this
    is the per-engine ownership view, reconstructed from the engine's own
    trade history, so the UI can show e.g. that a second engine on the same
    account is flat even though the account holds another engine's position.
    """
    engine = entry.get("engine")
    ledger = getattr(getattr(engine, "execution", None), "ledger", None)
    instrument = getattr(engine, "instrument", None)
    if ledger is None or instrument is None:
        return None
    try:
        qty = float(ledger.quantity(instrument.id) or 0)
    except Exception as exc:  # noqa: BLE001 — best-effort monitor field
        logger.warning("Owned-position read failed for %s: %s", entry["engine_id"], exc)
        return None
    if abs(qty) < 1e-12:
        return {"side": None, "quantity": 0.0}
    return {"side": "long" if qty > 0 else "short", "quantity": abs(qty)}


def list_engines() -> list[dict[str, Any]]:
    result = []
    # Iterate under the registry lock: start_engine/delete_engine mutate
    # _engines under _registry_lock, and this function runs sync on the
    # FastAPI threadpool, so concurrent mutation during iteration would raise
    # "dictionary changed size during iteration". Build the snapshot under the
    # lock, then return it.
    with _registry_lock:
        items = list(_engines.items())
    for eid, entry in items:
        _sync_trades(entry)
        item = {
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
            "trades": list(entry.get("trades", []) or [])[:50],
            "owned_position": _owned_position(entry),
        }
        if entry["status"] in {"warmup", "running", "restarting"}:
            position, last_price = _live_position_snapshot(entry)
            item["position"] = position
            item["last_price"] = last_price
        else:
            item["position"] = None
            item["last_price"] = None
        result.append(item)
    return result


def get_engine(engine_id: str) -> dict[str, Any] | None:
    return _engines.get(engine_id)


async def emergency_halt_all(reason: str) -> dict[str, Any]:
    """Persist the master halt, cancel tracked orders, and stop all engines."""
    state = GlobalRiskControl().update(halted=True, reason=reason)
    canceled: list[str] = []
    errors: list[str] = []
    # Snapshot the active engine ids under the registry lock so a concurrent
    # start_engine/delete_engine cannot mutate _engines during iteration
    # (RuntimeError: dictionary changed size during iteration). The per-engine
    # stop work below is awaited outside the lock to avoid blocking the
    # threadpool while waiting on broker I/O.
    with _registry_lock:
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
