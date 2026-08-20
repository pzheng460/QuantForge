"""CCXT market data and execution adapter.

Handles fetching warmup klines and subscribing to live kline streams
using ccxt for historical data.  The actual live WebSocket kline feed
should be wired in by the caller (e.g. via QuantForge engine connectors).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide
from quantforge.domain.instruments import (
    AssetClass,
    CryptoFuture,
    CryptoPerpetual,
    CryptoSpot,
    InstrumentId,
)
from quantforge.domain.timeframes import timeframe_to_seconds
from quantforge.execution import SubmissionOutcomeUnknown
from quantforge.strategy.bar import Bar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CcxtPositionError(RuntimeError):
    """Raised when a position query fails transiently — callers must NOT
    treat this as "flat" (that would risk stacking a duplicate position)."""


#: ccxt exchanges that accept a client-order-id on create_order for idempotency.
_IDEMPOTENCY_SUPPORTED = {"binance", "okx", "bybit", "bitget", "kucoin", "gate"}


def _as_bool(value: object, default: bool = True) -> bool:
    """Robustly coerce CCXT market flags (True/1/"true" → True; everything
    else including "false"/"0" → False)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def instrument_from_ccxt_market(market: dict, *, venue: str):
    symbol = str(market["symbol"])
    common = {
        "base_currency": str(market.get("base") or ""),
        "quote_currency": str(market.get("quote") or "USDT"),
    }
    market_type = str(market.get("type") or "").lower()
    if market_type == "spot":
        return CryptoSpot(
            id=InstrumentId(symbol, AssetClass.CRYPTO_SPOT, venue),
            **common,
        )
    derivative = {
        **common,
        "settlement_currency": str(market.get("settle") or "USDT"),
        "contract_size": float(market.get("contractSize") or 1),
        "linear": _as_bool(market.get("linear"), default=True),
        "max_leverage": float(market.get("maxLeverage") or 1),
    }
    if market_type == "future":
        expiry_ms = market.get("expiry")
        if not expiry_ms:
            raise ValueError(f"delivery future lacks expiry: {symbol}")
        expiration = datetime.fromtimestamp(
            float(expiry_ms) / 1000, tz=timezone.utc
        ).date()
        return CryptoFuture(
            id=InstrumentId(symbol, AssetClass.CRYPTO_FUTURE, venue),
            expiration=expiration,
            currency=derivative["settlement_currency"],
            **derivative,
        )
    if market_type == "swap":
        return CryptoPerpetual(
            id=InstrumentId(symbol, AssetClass.CRYPTO_PERPETUAL, venue),
            currency=derivative["settlement_currency"],
            **derivative,
        )
    raise ValueError(f"unsupported CCXT market type: {market_type or 'unknown'}")


# Timeframe → seconds lives in quantforge/domain/timeframes.py (single
# source of truth) and is imported above as ``timeframe_to_seconds``.


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
    cancel_check: Callable[[], None] | None = None,
) -> list[list]:
    """Canonical OHLCV fetcher used by both backtest and live engines.

    Pages through ccxt's ``fetch_ohlcv`` from ``since_ms`` until ``end_ms``,
    dedups by timestamp, sorts ascending. Returns the raw ccxt OHLCV
    rows (``[ts, open, high, low, close, volume]``). Callers that need
    ``BarData`` should map over the result.

    ``cancel_check`` is invoked before each page; it raises to abort a long
    pagination (the dashboard jobs use it so cancellation stays effective
    during the data-fetch phase).

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

    # Drop only the currently-in-progress bar — exchanges return it as the
    # last row of fetch_ohlcv with partial OHLC. If we let it through, warmup
    # would seed indicators with a half-baked candle and the live poll would
    # re-process that same bar once it closes (with completely different
    # OHLC). Backtest never sees a partial bar, so this is a
    # backtest-vs-live divergence we need to kill.
    #
    # The boundary is the START of the forming bar: any bar with
    # ts >= (now // bar) * bar is partial. (Using the previous bar's start
    # would ALSO drop the most recent completed bar, lagging live signals by
    # a full bar.)
    now_ms = int(time.time() * 1000)
    current_start_ms = (now_ms // bar_ms) * bar_ms
    effective_end_ms = min(end_ms, current_start_ms)

    seen: set[int] = set()
    unique: list[list] = []
    current = since_ms
    while current < end_ms:
        if cancel_check is not None:
            cancel_check()
        chunk = _retry_transient(
            lambda: exchange.fetch_ohlcv(
                symbol,
                timeframe,
                since=current,
                limit=page_limit,
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
) -> list[Bar]:
    """Fetch the last ``num_bars`` historical bars for indicator warmup.

    Thin wrapper around :func:`fetch_klines` that anchors to "now" and
    converts to :class:`quantforge.strategy.bar.Bar`.
    """
    tf_sec = timeframe_to_seconds(timeframe)
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(seconds=tf_sec * num_bars)
    since_ms = int(since_dt.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    raw = fetch_klines(
        symbol=symbol,
        exchange_id=exchange_id,
        timeframe=timeframe,
        since_ms=since_ms,
        end_ms=end_ms,
    )

    bars = [
        Bar(
            timestamp=b[0],
            open=b[1],
            high=b[2],
            low=b[3],
            close=b[4],
            volume=b[5],
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

    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        demo: bool = True,
        margin_mode: str = "cross",
    ) -> None:
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.demo = demo
        if margin_mode not in ("cross", "isolated"):
            raise ValueError(f"margin_mode must be 'cross' or 'isolated', got {margin_mode!r}")
        self.margin_mode = margin_mode
        # ``True`` for perp/future symbols (contain ":"); spot has no leverage.
        self._is_derivative_symbol = ":" in symbol
        self._leverage_set: float | None = None
        self._margin_mode_set: str | None = None
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

    def close(self) -> None:
        """Close the underlying ccxt exchange's HTTP session, if any.

        ccxt exchanges may hold an aiohttp/requests session with pooled
        sockets; closing it on shutdown prevents leaking file descriptors
        when connectors are churned (e.g. backtest/dashboard runs that spin
        up many short-lived exchanges). Stub/test exchanges without a
        ``close`` method are a no-op.
        """
        exchange = getattr(self, "_exchange", None)
        closer = getattr(exchange, "close", None)
        if callable(closer):
            closer()

    def __del__(self) -> None:
        """Best-effort cleanup if :meth:`close` was not called explicitly.

        Never raises: a partially-constructed instance (``_create_exchange``
        failed before ``self._exchange`` was set) or interpreter teardown must
        not propagate out of ``__del__``.
        """
        try:
            self.close()
        except Exception:  # noqa: BLE001 — __del__ must never raise
            pass

    def fetch_quote(self) -> dict | None:
        """Fetch a real-time bid/ask quote for ``self.symbol`` from ccxt.

        Returns ``{"bid": float, "ask": float, "time": datetime | None}`` using
        the ticker's live bid/ask when present. When the book is thin (bid or
        ask missing/zero) it falls back to the last trade price as a price
        reference BUT carries ``time=None``, so the upstream quote-age gate
        (``require_fresh_quote``) fails closed instead of accepting a synthetic
        zero-spread quote stamped with a fresh exchange time — mirroring
        :meth:`SchwabConnector.get_quote_bid_ask`'s ``time=None`` fail-closed
        semantics. Returns ``None`` when the exchange call fails or yields no
        usable price, so callers can decide how to degrade (never fabricate a
        price here).
        """
        try:
            ticker = self._exchange.fetch_ticker(self.symbol)
        except Exception:  # noqa: BLE001 — quote is best-effort
            logger.warning("fetch_ticker failed for %s", self.symbol, exc_info=True)
            return None
        try:
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            # Only a real two-sided book is trustworthy. When bid or ask is
            # missing/zero the book is thin: fall back to the last trade price
            # as a *priceless* reference but carry NO timestamp, so the
            # freshness/spread gates fail closed rather than accepting a
            # synthetic zero-spread quote (spread=0 <= max_spread_pct) that
            # looks fresh because it reuses the exchange timestamp.
            real_book = bool(bid) and bool(ask)
            if not real_book:
                last = ticker.get("last")
                if not last:
                    return None
                bid = ask = last
            ts = ticker.get("timestamp")
            return {
                "bid": float(bid),
                "ask": float(ask),
                "time": (
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    if real_book and ts
                    else None
                ),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Unusable ticker payload for %s: %s", self.symbol, exc)
            return None

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
        client_order_id: str | None = None,
    ) -> dict:
        """Submit via Bitget's UTA v3 place-order endpoint.

        Returns a dict in the same shape ccxt's create_order would, with
        ``id`` and ``status`` keys so callers don't care about the path.
        """
        category = self._bitget_uta_category()
        params: dict = {
            "symbol": self._bitget_uta_symbol(),
            "category": category,
            "side": side.upper(),  # Bitget UTA wants BUY / SELL
            "orderType": order_type,
            "qty": str(qty),
            # A stable idempotency key (the intent id when known) lets Bitget
            # reject a duplicate submission of the same order after a timeout.
            "clientOid": client_order_id or f"qf-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
        }
        if order_type == "limit":
            if price is None:
                raise ValueError("limit order requires price")
            params["price"] = str(price)
        if reduce_only and category != "SPOT":
            params["reduceOnly"] = "YES"
        # UTA has no standalone setMarginMode endpoint: the margin mode is
        # carried per order on place-order (futures only, 'crossed'/'isolated').
        if category != "SPOT":
            params["marginMode"] = "crossed" if self.margin_mode == "cross" else "isolated"

        logger.info(
            "Bitget UTA placeOrder: %s",
            {k: v for k, v in params.items() if k != "clientOid"},
        )
        resp = self._exchange.privateUtaPostV3TradePlaceOrder(params)
        code = resp.get("code")
        if code not in (None, "0", 0, "00000"):
            raise RuntimeError(
                f"Bitget UTA rejected order (code={code}): {resp.get('msg')}"
            )
        data = resp.get("data") or {}
        order_id = data.get("orderId")
        if not order_id:
            # The venue accepted the request (HTTP 2xx) without returning an
            # id: the order may well be live. Unknown outcome, never retry.
            raise SubmissionOutcomeUnknown(
                "Bitget UTA accepted order without an order id"
            )
        return {
            "id": order_id,
            "clientOrderId": data.get("clientOid"),
            "status": "submitted",
            "raw": resp,
        }

    # ─── Leverage and margin mode ────────────────────────────────────────────

    def ensure_leverage(self, leverage: float) -> None:
        """Push the intent's leverage to the exchange before trading.

        The risk engine caps ``leverage``; this transmits the value so the
        exchange does not silently fall back to its account default (which may
        exceed the sanctioned cap). Failures fail closed for live derivative
        symbols: an order approved for a low leverage must never be submitted
        while the exchange is still at an unknown/default (possibly far
        higher) leverage, because a smaller adverse move then liquidates the
        position. Demo mode logs and continues (no real money is at stake);
        spot symbols have no leverage and also continue.
        """
        if self._leverage_set == leverage:
            return
        try:
            self._exchange.set_leverage(leverage, self.symbol)
        except Exception as exc:  # noqa: BLE001 — exchange capability variance
            if self._is_derivative_symbol and not self.demo:
                logger.error(
                    "Unable to set %s leverage to %s (%s) — refusing to trade "
                    "at unknown leverage",
                    self.symbol,
                    leverage,
                    exc,
                )
                raise RuntimeError(
                    f"unable to set {self.symbol} leverage to {leverage}; "
                    "refusing to trade at unknown leverage"
                ) from exc
            # Demo or spot: note it and proceed (orders are simulated, or
            # leverage is not applicable). Do NOT memoize — retry on the next
            # submission in case the exchange becomes able to honor it.
            logger.warning(
                "Unable to set %s leverage to %s (%s) — using account default",
                self.symbol,
                leverage,
                exc,
            )
            return
        self._leverage_set = leverage
        logger.info("Set %s leverage to %s", self.symbol, leverage)

    def ensure_margin_mode(self) -> None:
        """Explicitly set the strategy's cross/isolated margin mode.

        Bitget UTA carries ``marginMode`` per order on the place-order
        endpoint (there is no UTA ``setMarginMode`` call), so the UTA path
        needs no pre-trade call — the per-order parameter on
        :meth:`_bitget_uta_place_order` is the source of truth. Every other
        venue uses ccxt's unified ``set_margin_mode`` and fails closed on live
        derivative symbols when it cannot be pushed: trading at the exchange
        account's default margin mode (which may be isolated when the strategy
        expects cross, or vice versa) is a real-money leverage hazard.
        """
        if self._margin_mode_set == self.margin_mode:
            return
        if self.is_bitget_uta or not self._is_derivative_symbol:
            # UTA: per-order marginMode; spot: no margin mode to set.
            self._margin_mode_set = self.margin_mode
            return
        try:
            self._exchange.set_margin_mode(self.margin_mode, self.symbol)
        except Exception as exc:  # noqa: BLE001 — exchange capability variance
            if not self.demo:
                logger.error(
                    "Unable to set %s margin mode to %s (%s) — refusing to "
                    "trade at an unknown margin mode",
                    self.symbol,
                    self.margin_mode,
                    exc,
                )
                raise RuntimeError(
                    f"unable to set {self.symbol} margin mode to "
                    f"{self.margin_mode}; refusing to trade at unknown margin mode"
                ) from exc
            logger.warning(
                "Unable to set %s margin mode to %s (%s) — demo, using default",
                self.symbol,
                self.margin_mode,
                exc,
            )
            return
        self._margin_mode_set = self.margin_mode
        logger.info("Set %s margin mode to %s", self.symbol, self.margin_mode)

    # ─── Public API ──────────────────────────────────────────────────────────

    def submit_market_order(
        self,
        side: str,
        qty: float,
        reduce_only: bool = False,
        leverage: float = 1,
        client_order_id: str | None = None,
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
        leverage : float
            Requested leverage for the position (applied where supported).
        client_order_id : str | None
            Stable idempotency key (the intent id) so a timeout retry of the
            same intent cannot double-fill at the venue.
        """
        self.ensure_leverage(leverage)
        self.ensure_margin_mode()
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
            result = self._submit_unknown_aware(
                lambda: self._bitget_uta_place_order(
                    side=side,
                    qty=qty,
                    order_type="market",
                    reduce_only=reduce_only,
                    client_order_id=client_order_id,
                )
            )
        else:
            params: dict = {"reduceOnly": True} if reduce_only else {}
            self._idempotency_params(params, client_order_id)
            result = self._submit_unknown_aware(
                lambda: self._exchange.create_order(
                    self.symbol, "market", side, qty, params=params
                )
            )
        logger.info(
            "Order result: id=%s status=%s", result.get("id"), result.get("status")
        )
        return result

    def _submit_unknown_aware(self, action: Callable[[], dict]) -> dict:
        """Execute an order submission, mapping network/timeout failures to an
        unknown outcome.

        A ccxt request that dies on the wire may still have been accepted by
        the venue (the response is what timed out). Treating it as a definitive
        rejection makes ``ExecutionService`` release the risk reservation and
        re-authorize the same intent on retry — double-fill. So ``NetworkError``
        / ``RequestTimeout`` become ``SubmissionOutcomeUnknown``, which the
        execution service re-raises WITHOUT releasing. Genuine local/rejection
        errors (``InvalidOrder``, ``InsufficientFunds``, ``AuthenticationError``)
        still propagate normally so never-sent orders DO release.
        """
        import ccxt

        try:
            return action()
        except SubmissionOutcomeUnknown:
            raise
        except (ccxt.NetworkError, ccxt.RequestTimeout, TimeoutError) as exc:
            raise SubmissionOutcomeUnknown(str(exc)) from exc

    def submit_limit_order(
        self,
        side: str,
        qty: float,
        price: float,
        reduce_only: bool = False,
        leverage: float = 1,
        client_order_id: str | None = None,
    ) -> dict:
        """Submit a limit order through UTA or the generic CCXT adapter."""
        if price <= 0:
            raise ValueError("limit price must be positive")
        self.ensure_leverage(leverage)
        self.ensure_margin_mode()
        if self.is_bitget_uta:
            return self._submit_unknown_aware(
                lambda: self._bitget_uta_place_order(
                    side=side,
                    qty=qty,
                    order_type="limit",
                    price=price,
                    reduce_only=reduce_only,
                    client_order_id=client_order_id,
                )
            )
        params: dict = {"reduceOnly": True} if reduce_only else {}
        self._idempotency_params(params, client_order_id)
        return self._submit_unknown_aware(
            lambda: self._exchange.create_order(
                self.symbol,
                "limit",
                side,
                qty,
                price,
                params,
            )
        )

    def _idempotency_params(self, params: dict, client_order_id: str | None) -> None:
        """Attach a client order id where the exchange supports it, so a
        caller retrying a timed-out order cannot double-fill.

        The idempotency key is the intent id when available (stable across a
        retry of the SAME intent); a fresh uuid is only a fallback for direct
        connector callers that have no intent.
        """
        if self.exchange_id in _IDEMPOTENCY_SUPPORTED:
            key = client_order_id or f"qf-{uuid.uuid4().hex}"
            params.setdefault("clientOrderId", key)

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
                resp = self._exchange.privateUtaGetV3PositionCurrentPosition(
                    {
                        "symbol": self._bitget_uta_symbol(),
                        "category": category,
                    }
                )
                data = resp.get("data") or {}
                # Bitget UTA returns either a list under data.list / data or
                # a single object — be defensive about shape.
                positions = data.get("list") if isinstance(data, dict) else data
                if not positions:
                    return None
                if isinstance(positions, dict):
                    positions = [positions]
                for p in positions:
                    qty = float(
                        p.get("total") or p.get("size") or p.get("contracts") or 0
                    )
                    if qty > 0:
                        # holdSide: long/short on Bitget; entryPrice / openPriceAvg
                        side = p.get("holdSide") or p.get("side")
                        entry = float(p.get("openPriceAvg") or p.get("entryPrice") or 0)
                        upnl = float(
                            p.get("unrealisedPL") or p.get("unrealizedPnl") or 0
                        )
                        return {
                            "side": side,
                            "contracts": qty,
                            "entryPrice": entry,
                            "unrealizedPnl": upnl,
                        }
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning("UTA fetch_position failed (%s)", exc)
                raise CcxtPositionError(
                    f"UTA position query failed; cannot treat as flat: {exc}"
                ) from exc
        # Non-bitget / non-UTA path
        try:
            positions = self._exchange.fetch_positions([self.symbol])
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_positions failed (%s)", exc)
            raise CcxtPositionError(
                f"position query failed; cannot treat as flat: {exc}"
            ) from exc
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

    def cancel_all_orders(self) -> list:
        """Cancel all open orders for this engine symbol."""
        return list(self._exchange.cancel_all_orders(self.symbol) or [])


class CcxtExecutionAdapter:
    """Translate canonical single-leg intents into CCXT market orders."""

    def __init__(self, connector: CcxtConnector):
        self.connector = connector

    def submit(self, intent: OrderIntent | MultiLegOrderIntent) -> str:
        if isinstance(intent, MultiLegOrderIntent):
            raise ValueError("CCXT multi-leg atomic orders are not supported")
        if intent.instrument.id.venue.lower() != self.connector.exchange_id.lower():
            raise ValueError("intent venue does not match CCXT connector")
        # Re-check the instrument-level leverage cap right before submission;
        # the intent's requested leverage must be respected by the exchange.
        inst = intent.instrument
        if (
            inst.id.asset_class
            in {AssetClass.CRYPTO_PERPETUAL, AssetClass.CRYPTO_FUTURE}
            and intent.leverage > getattr(inst, "max_leverage", 1)
        ):
            raise ValueError("intent leverage exceeds instrument max leverage")
        side = "buy" if intent.side is OrderSide.BUY else "sell"
        if intent.order_type.value == "market":
            result = self.connector.submit_market_order(
                side,
                intent.quantity,
                reduce_only=intent.reduce_only,
                leverage=intent.leverage,
                client_order_id=intent.intent_id,
            )
        elif intent.order_type.value == "limit" and intent.limit_price is not None:
            result = self.connector.submit_limit_order(
                side,
                intent.quantity,
                intent.limit_price,
                reduce_only=intent.reduce_only,
                leverage=intent.leverage,
                client_order_id=intent.intent_id,
            )
        else:
            raise ValueError("CCXT adapter accepts market and priced limit intents")
        order_id = str(result.get("id") or "")
        if not order_id:
            # Returned success without an id: the order may have been accepted.
            raise SubmissionOutcomeUnknown("exchange accepted order without an id")
        return order_id
