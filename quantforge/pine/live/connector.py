"""Exchange connector wrapper for Pine live trading.

Handles fetching warmup klines and subscribing to live kline streams
using ccxt for historical data.  The actual live WebSocket kline feed
should be wired in by the caller (e.g. via QuantForge engine connectors).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from quantforge.pine.interpreter.context import BarData

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Timeframe → seconds mapping. Single source of truth for the whole
# codebase — anywhere else that needs bar duration (window math in
# apps/dashboard/backend/jobs.py, sleep cycles in the live engine,
# warmup window in cli.py) should derive from this.
_TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


def timeframe_to_seconds(tf: str) -> int:
    """Convert a timeframe string like '15m' to seconds."""
    if tf in _TF_SECONDS:
        return _TF_SECONDS[tf]
    raise ValueError(f"Unsupported timeframe: {tf}")


def _retry_transient(
    op: Callable[[], T],
    *,
    label: str,
    transient_errors: tuple[type[BaseException], ...],
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Retry transient exchange/network calls with short exponential backoff."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except transient_errors as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (%s/%s): %s — retrying in %.1fs",
                label,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def fetch_klines(
    symbol: str,
    exchange_id: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
    page_limit: int = 1000,
) -> list[list]:
    """Canonical OHLCV fetcher used by both backtest and live engines.

    Pages through ccxt's ``fetch_ohlcv`` from ``since_ms`` until ``end_ms``,
    dedups by timestamp, sorts ascending. Returns the raw ccxt OHLCV
    rows (``[ts, open, high, low, close, volume]``). Callers that need
    ``BarData`` should map over the result.

    Centralising this prevents drift between backtest's old
    ``cli._fetch_ohlcv`` and live's old ``fetch_warmup_bars``, which
    paginated and de-duped slightly differently and could disagree on
    edge bars.
    """
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")

    exchange = exchange_cls({"enableRateLimit": True, "timeout": 30_000})
    transient_errors = (
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        TimeoutError,
        ConnectionError,
    )
    _retry_transient(
        exchange.load_markets,
        label=f"{exchange_id}.load_markets",
        transient_errors=transient_errors,
    )

    bar_ms = timeframe_to_seconds(timeframe) * 1000
    window_ms = bar_ms * page_limit  # span covered per request

    # Drop the currently-in-progress bar — exchanges typically return it
    # as the last row of fetch_ohlcv with partial OHLC. If we let it
    # through, warmup would seed indicators with a half-baked candle
    # and the live poll would re-process that same bar once it closes
    # (with completely different OHLC). Backtest never sees a partial
    # bar, so this is a backtest-vs-live divergence we need to kill.
    now_ms = int(time.time() * 1000)
    last_closed_start_ms = ((now_ms // bar_ms) - 1) * bar_ms
    effective_end_ms = min(end_ms, last_closed_start_ms)

    seen: set[int] = set()
    unique: list[list] = []
    current = since_ms
    while current < end_ms:
        chunk = _retry_transient(
            lambda: exchange.fetch_ohlcv(
                symbol, timeframe, since=current, limit=page_limit,
            ),
            label=f"{exchange_id}.fetch_ohlcv({symbol},{timeframe})",
            transient_errors=transient_errors,
        )
        if not chunk:
            # Sparse exchange / no data here. Skip forward by a window so
            # we don't loop forever on dead ranges. Same fallback the
            # backend's historical pager uses.
            current += window_ms
            continue
        for bar in chunk:
            ts = bar[0]
            if ts >= effective_end_ms or ts in seen:
                continue
            seen.add(ts)
            unique.append(bar)
        last_ts = chunk[-1][0]
        if last_ts <= current:
            current += window_ms
        else:
            current = last_ts + 1

    unique.sort(key=lambda b: b[0])
    return unique


def fetch_warmup_bars(
    symbol: str,
    exchange_id: str,
    timeframe: str,
    num_bars: int = 500,
) -> list[BarData]:
    """Fetch the last ``num_bars`` historical bars for indicator warmup.

    Thin wrapper around :func:`fetch_klines` that anchors to "now" and
    converts to ``BarData``.
    """
    tf_sec = timeframe_to_seconds(timeframe)
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(seconds=tf_sec * num_bars)
    since_ms = int(since_dt.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    raw = fetch_klines(
        symbol=symbol, exchange_id=exchange_id, timeframe=timeframe,
        since_ms=since_ms, end_ms=end_ms,
    )

    bars = [
        BarData(
            open=b[1], high=b[2], low=b[3], close=b[4],
            volume=b[5], time=b[0] // 1000,
        )
        for b in raw[-num_bars:]
    ]
    logger.info("Fetched %d warmup bars for %s (%s)", len(bars), symbol, exchange_id)
    return bars


class CcxtConnector:
    """Wraps a ccxt exchange instance for order submission and position queries.

    Used by :class:`OrderBridge` to submit real orders when *demo* mode is off.

    Parameters
    ----------
    exchange_id : str
        ccxt exchange id (e.g. ``"bitget"``).
    symbol : str
        Trading symbol in ccxt format (e.g. ``"BTC/USDT:USDT"``).
    demo : bool
        If ``True``, use sandbox/demo API keys.
    """

    def __init__(self, exchange_id: str, symbol: str, demo: bool = True) -> None:
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.demo = demo
        self._exchange = self._create_exchange()

    def _create_exchange(self):
        import ccxt

        from quantforge.constants import settings

        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Exchange '{self.exchange_id}' not found in ccxt")

        config: dict = {"enableRateLimit": True}

        # Load API keys from settings
        try:
            if self.exchange_id == "bitget":
                if self.demo:
                    config["apiKey"] = settings.BITGET.DEMO.API_KEY
                    config["secret"] = settings.BITGET.DEMO.SECRET
                    config["password"] = settings.BITGET.DEMO.PASSPHRASE
                    # Bitget UTA demo uses paptrading header (not sandbox URLs)
                    config["headers"] = {"paptrading": "1"}
                else:
                    # Matches .keys/.secrets.toml layout: [BITGET.LIVE].
                    config["apiKey"] = settings.BITGET.LIVE.API_KEY
                    config["secret"] = settings.BITGET.LIVE.SECRET
                    config["password"] = settings.BITGET.LIVE.PASSPHRASE
            elif self.exchange_id == "binance":
                if self.demo:
                    config["apiKey"] = settings.BINANCE.TESTNET.API_KEY
                    config["secret"] = settings.BINANCE.TESTNET.SECRET
                else:
                    config["apiKey"] = settings.BINANCE.API_KEY
                    config["secret"] = settings.BINANCE.SECRET
            elif self.exchange_id == "okx":
                if self.demo:
                    config["apiKey"] = settings.OKX.DEMO_1.API_KEY
                    config["secret"] = settings.OKX.DEMO_1.SECRET
                    config["password"] = settings.OKX.DEMO_1.PASSPHRASE
                else:
                    config["apiKey"] = settings.OKX.LIVE.ACCOUNT1.API_KEY
                    config["secret"] = settings.OKX.LIVE.ACCOUNT1.SECRET
                    config["password"] = settings.OKX.LIVE.ACCOUNT1.PASSPHRASE
            elif self.exchange_id == "bybit":
                if self.demo:
                    config["apiKey"] = settings.BYBIT.TESTNET.API_KEY
                    config["secret"] = settings.BYBIT.TESTNET.SECRET
                else:
                    config["apiKey"] = settings.BYBIT.API_KEY
                    config["secret"] = settings.BYBIT.SECRET
        except (AttributeError, KeyError) as exc:
            # In demo: warn and continue — user may be experimenting with no keys.
            # In LIVE: refuse to construct the exchange — silently swallowing
            # missing creds and letting ccxt explode on the first call is the
            # pattern that just burned us with the 'requires "apiKey"' crash.
            if self.demo:
                logger.warning(
                    "API keys not found for %s (demo=True) — order submission will fail. (%s)",
                    self.exchange_id,
                    exc,
                )
            else:
                raise RuntimeError(
                    f"Cannot start LIVE engine for {self.exchange_id}: "
                    f"API keys missing in .keys/.secrets.toml (looked up "
                    f"settings.{self.exchange_id.upper()}.LIVE.*; got: {exc})"
                ) from exc

        exchange = exchange_cls(config)

        # Bitget demo uses paptrading header (set above), not sandbox mode.
        # Other exchanges may still need set_sandbox_mode.
        if self.demo and self.exchange_id != "bitget":
            exchange.set_sandbox_mode(True)

        exchange.load_markets()
        return exchange

    # ─── Bitget UTA helpers ──────────────────────────────────────────────────

    @property
    def is_bitget_uta(self) -> bool:
        """True if this connector is talking to a Bitget Unified Trading Account.

        Detected lazily on first access by probing the UTA balance endpoint;
        accounts in classic mode return a 40085 error and we mark them as
        non-UTA so subsequent calls go through ccxt's generic Classic path.
        """
        if self.exchange_id != "bitget":
            return False
        cached = getattr(self, "_uta_cached", None)
        if cached is not None:
            return cached
        try:
            self._exchange.privateUtaGetV3AccountAssets()
            self._uta_cached = True
        except Exception as exc:  # noqa: BLE001 — any failure means "not UTA"
            logger.debug("Bitget UTA probe failed (%s) — treating as classic", exc)
            self._uta_cached = False
        return self._uta_cached

    def _bitget_uta_category(self) -> str:
        """Map ccxt symbol (e.g. 'BTC/USDT:USDT') to Bitget UTA category."""
        if ":USDT" in self.symbol:
            return "USDT-FUTURES"
        if ":USDC" in self.symbol:
            return "USDC-FUTURES"
        return "SPOT"

    def _bitget_uta_symbol(self) -> str:
        """Convert ccxt-style 'BTC/USDT' or 'BTC/USDT:USDT' to Bitget 'BTCUSDT'."""
        base = self.symbol.split(":")[0]
        return base.replace("/", "")

    def _bitget_uta_place_order(
        self,
        side: str,
        qty: float,
        order_type: str = "market",
        price: float | None = None,
        reduce_only: bool = False,
    ) -> dict:
        """Submit via Bitget's UTA v3 place-order endpoint.

        Returns a dict in the same shape ccxt's create_order would, with
        ``id`` and ``status`` keys so callers don't care about the path.
        """
        import time as _time

        params: dict = {
            "symbol": self._bitget_uta_symbol(),
            "category": self._bitget_uta_category(),
            "side": side.upper(),  # Bitget UTA wants BUY / SELL
            "orderType": order_type,
            "qty": str(qty),
            "clientOid": f"qf-{int(_time.time() * 1000)}",
        }
        if order_type == "limit":
            if price is None:
                raise ValueError("limit order requires price")
            params["price"] = str(price)
        if reduce_only and params["category"] != "SPOT":
            params["reduceOnly"] = "YES"

        logger.info(
            "Bitget UTA placeOrder: %s",
            {k: v for k, v in params.items() if k != "clientOid"},
        )
        resp = self._exchange.privateUtaPostV3TradePlaceOrder(params)
        data = resp.get("data") or {}
        return {
            "id": data.get("orderId"),
            "clientOrderId": data.get("clientOid"),
            "status": "submitted",
            "raw": resp,
        }

    # ─── Public API ──────────────────────────────────────────────────────────

    def submit_market_order(
        self, side: str, qty: float, reduce_only: bool = False
    ) -> dict:
        """Submit a market order.

        Parameters
        ----------
        side : str
            ``"buy"`` or ``"sell"``.
        qty : float
            Order quantity (in contracts or base currency).
        reduce_only : bool
            If ``True``, only reduce existing position.
        """
        logger.info(
            "Submitting MARKET %s %.6f %s (reduce_only=%s)",
            side.upper(),
            qty,
            self.symbol,
            reduce_only,
        )
        # Bitget UTA: ccxt's generic create_order routes through the Classic
        # spot endpoint which rejects UTA accounts with 40085. Route directly
        # to the UTA place-order endpoint instead.
        if self.is_bitget_uta:
            result = self._bitget_uta_place_order(
                side=side, qty=qty, order_type="market", reduce_only=reduce_only
            )
        else:
            params: dict = {"reduceOnly": True} if reduce_only else {}
            result = self._exchange.create_order(
                self.symbol, "market", side, qty, params=params
            )
        logger.info(
            "Order result: id=%s status=%s", result.get("id"), result.get("status")
        )
        return result

    def submit_limit_order(
        self, side: str, qty: float, price: float, reduce_only: bool = False
    ) -> dict:
        """Submit a limit order."""
        logger.info(
            "Submitting LIMIT %s %.6f @ %.2f %s",
            side.upper(),
            qty,
            price,
            self.symbol,
        )
        if self.is_bitget_uta:
            result = self._bitget_uta_place_order(
                side=side, qty=qty, order_type="limit", price=price,
                reduce_only=reduce_only,
            )
        else:
            params: dict = {"reduceOnly": True} if reduce_only else {}
            result = self._exchange.create_order(
                self.symbol, "limit", side, qty, price, params=params
            )
        logger.info(
            "Order result: id=%s status=%s", result.get("id"), result.get("status")
        )
        return result

    def get_position(self) -> dict | None:
        """Get current position for the symbol.

        Returns a dict with ``side``, ``contracts``, ``entryPrice``, or
        ``None`` if flat. For Bitget UTA SPOT we always return None (spot
        accounts don't track positions — holdings are queried via balance).
        For Bitget UTA FUTURES we route to the UTA position endpoint.
        """
        if self.is_bitget_uta:
            category = self._bitget_uta_category()
            if category == "SPOT":
                return None
            # Futures on UTA: ccxt's fetch_positions routes through Classic
            # v2 mix which UTA rejects with 40085. Call UTA endpoint directly.
            try:
                resp = self._exchange.privateUtaGetV3PositionCurrentPosition({
                    "symbol": self._bitget_uta_symbol(),
                    "category": category,
                })
                data = resp.get("data") or {}
                # Bitget UTA returns either a list under data.list / data or
                # a single object — be defensive about shape.
                positions = data.get("list") if isinstance(data, dict) else data
                if not positions:
                    return None
                if isinstance(positions, dict):
                    positions = [positions]
                for p in positions:
                    qty = float(p.get("total") or p.get("size") or p.get("contracts") or 0)
                    if qty > 0:
                        # holdSide: long/short on Bitget; entryPrice / openPriceAvg
                        side = p.get("holdSide") or p.get("side")
                        entry = float(
                            p.get("openPriceAvg") or p.get("entryPrice") or 0
                        )
                        upnl = float(p.get("unrealisedPL") or p.get("unrealizedPnl") or 0)
                        return {
                            "side": side,
                            "contracts": qty,
                            "entryPrice": entry,
                            "unrealizedPnl": upnl,
                        }
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("UTA fetch_position failed (%s) — assuming flat", exc)
                return None
        # Non-bitget / non-UTA path
        try:
            positions = self._exchange.fetch_positions([self.symbol])
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_positions failed (%s) — assuming flat", exc)
            return None
        for pos in positions:
            contracts = float(pos.get("contracts", 0))
            if contracts > 0:
                return {
                    "side": pos.get("side"),
                    "contracts": contracts,
                    "entryPrice": float(pos.get("entryPrice", 0)),
                    "unrealizedPnl": float(pos.get("unrealizedPnl", 0)),
                }
        return None

    def get_ticker_price(self) -> float:
        """Get the current ticker price for the symbol."""
        ticker = self._exchange.fetch_ticker(self.symbol)
        return float(ticker.get("last", 0))


def ohlcv_to_bar(ohlcv: list) -> BarData:
    """Convert a single ccxt OHLCV list ``[ts, o, h, l, c, v]`` to BarData."""
    return BarData(
        open=ohlcv[1],
        high=ohlcv[2],
        low=ohlcv[3],
        close=ohlcv[4],
        volume=ohlcv[5],
        time=ohlcv[0] // 1000,
    )
