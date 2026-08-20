"""Native Charles Schwab Trader and Market Data API connector."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import secrets
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from quantforge.brokers.protocol import BrokerOrder

logger = logging.getLogger(__name__)

AUTH_BASE = "https://api.schwabapi.com/v1/oauth"
TRADER_BASE = "https://api.schwabapi.com/trader/v1"
MARKET_DATA_BASE = "https://api.schwabapi.com/marketdata/v1"

# Named network/token tuning constants (previously scattered magic numbers).
_REQUEST_TIMEOUT = 30.0
_GET_RETRY_ATTEMPTS = 3
_RETRY_MAX_DELAY = 5.0
_TOKEN_EXPIRY_BUFFER_SECONDS = 60
_DEFAULT_EXPIRES_IN = 1800
# POST paths whose terminal state is "unknown" on ANY transport failure.
_ORDER_URL_SUFFIX = "/orders"


def _as_positive_float(value: Any) -> float | None:
    """Coerce a quote field to a positive float, or None when unusable."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_quote_time(value: Any) -> datetime | None:
    """Parse Schwab's quoteTime (ISO string or epoch milliseconds) to UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


class SchwabError(RuntimeError):
    """Base class for sanitized Schwab failures."""


class SchwabAuthError(SchwabError):
    pass


class SchwabOrderError(SchwabError):
    pass


class SchwabAmbiguousOrderError(SchwabOrderError):
    """Submission outcome is unknown and must be reconciled manually."""


@dataclass(frozen=True)
class SchwabCredentials:
    app_key: str
    app_secret: str
    callback_url: str


@dataclass(frozen=True)
class SchwabAccount:
    account_hash: str
    account_type: str
    display_id: str


class SchwabTokenStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or Path.home() / ".quantforge/schwab/tokens.json")

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchwabAuthError("Schwab token store is unreadable") from exc

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # An existing directory created before the permissions policy keeps
        # its loose mode: mkdir(exist_ok=True) never narrows it (L11).
        try:
            mode = self.path.parent.stat().st_mode & 0o777
            if mode & 0o077:  # any group or world bits set
                self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = dict(token)
        payload["obtained_at"] = int(time.time())
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        self.path.chmod(0o600)
        logger.info("Schwab token store persisted to %s", self.path)


class SchwabOAuthClient:
    def __init__(
        self,
        credentials: SchwabCredentials,
        *,
        token_store: SchwabTokenStore | None = None,
        session=None,
    ) -> None:
        self.credentials = credentials
        self.token_store = token_store or SchwabTokenStore()
        self.session = session or requests.Session()

    def authorization_url(self, state: str | None = None) -> tuple[str, str]:
        state = state or secrets.token_urlsafe(32)
        params = urlencode(
            {
                "client_id": self.credentials.app_key,
                "redirect_uri": self.credentials.callback_url,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{AUTH_BASE}/authorize?{params}", state

    def exchange_code(self, code: str) -> dict[str, Any]:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.credentials.callback_url,
            }
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        basic = base64.b64encode(
            f"{self.credentials.app_key}:{self.credentials.app_secret}".encode()
        ).decode()
        try:
            response = self.session.request(
                "POST",
                f"{AUTH_BASE}/token",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data=data,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise SchwabAuthError("Schwab authentication request failed") from exc
        if response.status_code >= 400:
            raise SchwabAuthError(
                f"Schwab authentication rejected (HTTP {response.status_code})"
            )
        token = response.json()
        if not token.get("access_token"):
            raise SchwabAuthError("Schwab authentication returned no access token")
        self.token_store.save(token)
        return token


_STATUS_MAP = {
    "AWAITING_PARENT_ORDER": "pending",
    "AWAITING_CONDITION": "pending",
    "AWAITING_STOP_CONDITION": "pending",
    "AWAITING_MANUAL_REVIEW": "pending",
    "ACCEPTED": "accepted",
    "AWAITING_UR_OUT": "accepted",
    "PENDING_ACTIVATION": "accepted",
    "QUEUED": "accepted",
    "WORKING": "accepted",
    "PARTIALLY_FILLED": "partially_filled",
    "FILLED": "filled",
    "CANCELED": "canceled",
    "EXPIRED": "canceled",
    "REPLACED": "canceled",
    "REJECTED": "rejected",
}


class SchwabConnector:
    def __init__(
        self,
        credentials: SchwabCredentials,
        *,
        market_credentials: SchwabCredentials | None = None,
        account_hash: str | None = None,
        symbol: str | None = None,
        token_path: str | Path | None = None,
        market_token_path: str | Path | None = None,
        session=None,
        access_token: str | None = None,
        market_access_token: str | None = None,
    ) -> None:
        self.credentials = credentials
        self.market_credentials = market_credentials or credentials
        self.account_hash = account_hash
        self.symbol = self.normalize_symbol(symbol) if symbol else None
        self.session = session or requests.Session()
        self.token_store = SchwabTokenStore(
            token_path or Path.home() / ".quantforge/schwab/tokens-trading.json"
        )
        self.oauth = SchwabOAuthClient(
            credentials, token_store=self.token_store, session=self.session
        )
        self._token = self.token_store.load() or {}
        use_shared_token = market_credentials is None and market_token_path is None
        self._use_shared_token = use_shared_token
        self._token_lock = threading.RLock()
        self._orders_lock = threading.Lock()
        self.market_token_store = (
            self.token_store
            if use_shared_token
            else SchwabTokenStore(
                market_token_path
                or Path.home() / ".quantforge/schwab/tokens-market-data.json"
            )
        )
        self.market_oauth = (
            self.oauth
            if use_shared_token
            else SchwabOAuthClient(
                self.market_credentials,
                token_store=self.market_token_store,
                session=self.session,
            )
        )
        self._market_token = (
            self._token if use_shared_token else self.market_token_store.load() or {}
        )
        self._tracked_orders: dict[str, str] = {}
        if access_token:
            self._token = {
                "access_token": access_token,
                "expires_in": _DEFAULT_EXPIRES_IN,
            }
        if market_access_token:
            self._market_token = {
                "access_token": market_access_token,
                "expires_in": _DEFAULT_EXPIRES_IN,
            }
        if use_shared_token:
            # Keep both views pointing at the same token object so refresh
            # cannot split them into divergent tokens (see _access_token).
            self._market_token = self._token

    @property
    def authenticated(self) -> bool:
        return self.trading_authenticated and self.market_data_authenticated

    @property
    def trading_authenticated(self) -> bool:
        return bool(self._token.get("access_token"))

    @property
    def market_data_authenticated(self) -> bool:
        return bool(self._market_token.get("access_token"))

    def _access_token(self, *, market_data: bool = False) -> str:
        # Serialize refresh so concurrent callers cannot race the token
        # rotation or write back stale tokens.
        with self._token_lock:
            token = self._market_token if market_data else self._token
            oauth = self.market_oauth if market_data else self.oauth
            token_store = self.market_token_store if market_data else self.token_store
            obtained = float(token.get("obtained_at", time.time()))
            expires = float(token.get("expires_in", _DEFAULT_EXPIRES_IN))
            if time.time() >= obtained + expires - _TOKEN_EXPIRY_BUFFER_SECONDS:
                refresh_token = token.get("refresh_token")
                if not refresh_token:
                    raise SchwabAuthError("Schwab authorization is required")
                refreshed = oauth.refresh(str(refresh_token))
                if not refreshed.get("refresh_token"):
                    refreshed["refresh_token"] = refresh_token
                    token_store.save(refreshed)
                if self._use_shared_token:
                    # Shared token: keep every view consistent.
                    self._token = refreshed
                    self._market_token = refreshed
                elif market_data:
                    self._market_token = refreshed
                else:
                    self._token = refreshed
                token = refreshed
                logger.info(
                    "Schwab %s token refreshed",
                    "market-data" if market_data else "trading",
                )
            value = token.get("access_token")
            if not value:
                raise SchwabAuthError("Schwab authorization is required")
            return str(value)

    def _request(self, method: str, url: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {self._access_token(market_data=url.startswith(MARKET_DATA_BASE))}",
            "Accept": "application/json",
        }
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        is_order_submission = method == "POST" and url.endswith(_ORDER_URL_SUFFIX)
        try:
            response = None
            attempts = _GET_RETRY_ATTEMPTS if method == "GET" else 1
            for attempt in range(attempts):
                response = self.session.request(
                    method, url, headers=headers, timeout=_REQUEST_TIMEOUT, **kwargs
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt + 1 < attempts:
                    delay = min(
                        float(response.headers.get("Retry-After", 2**attempt)),
                        _RETRY_MAX_DELAY,
                    )
                    time.sleep(delay)
            if response is None:
                raise SchwabError("Schwab request produced no response")
        except requests.RequestException as exc:
            if is_order_submission:
                # The order may already be live at the broker. Any transport
                # failure (timeout, reset, conn drop, chunked-encoding error)
                # leaves the outcome unknown: surface it as ambiguous so the
                # execution layer never retries and double-fills.
                logger.error("Schwab order outcome unknown after transport failure")
                raise SchwabAmbiguousOrderError(
                    "Schwab order outcome is unknown (transport failure); "
                    "trading paused"
                ) from exc
            raise SchwabError("Schwab request failed") from exc
        if response.status_code in (401, 403):
            raise SchwabAuthError(
                f"Schwab authorization failed (HTTP {response.status_code})"
            )
        if is_order_submission and (
            response.status_code == 429 or response.status_code >= 500
        ):
            # A rate-limit 429 or gateway 5xx on an order POST means "the
            # gateway broke / throttled us", not "the order was rejected".
            # Schwab can have already accepted the order when such a response
            # is emitted, so the outcome is genuinely unknown. Surface it as
            # ambiguous: the execution layer keeps the reservation and never
            # re-submits, preventing a double fill.
            logger.error(
                "Schwab order outcome unknown after HTTP %s",
                response.status_code,
            )
            raise SchwabAmbiguousOrderError(
                f"Schwab order outcome is unknown (HTTP {response.status_code}); "
                "trading paused"
            )
        if response.status_code >= 400:
            error_type = (
                SchwabOrderError if _ORDER_URL_SUFFIX in url else SchwabError
            )
            raise error_type(f"Schwab API rejected request (HTTP {response.status_code})")
        return response

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        value = symbol.strip().upper()
        if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ.-" for ch in value):
            raise ValueError(f"Invalid US equity symbol: {symbol!r}")
        return value

    @staticmethod
    def shares_for_budget(budget_usd: float, price: float) -> int:
        if budget_usd <= 0 or price <= 0:
            raise ValueError("budget and price must be positive")
        shares = math.floor(budget_usd / price)
        if shares < 1:
            raise ValueError("position budget is smaller than one share")
        return shares

    def get_accounts(self) -> list[SchwabAccount]:
        response = self._request("GET", f"{TRADER_BASE}/accounts/accountNumbers")
        return [
            SchwabAccount(
                account_hash=str(row["hashValue"]),
                account_type=str(row.get("type", "")),
                display_id=str(row.get("accountNumber", ""))[-4:].rjust(4, "•"),
            )
            for row in response.json()
        ]

    def get_positions(self) -> list[dict]:
        account = self._require_account()
        response = self._request(
            "GET", f"{TRADER_BASE}/accounts/{account}", params={"fields": "positions"}
        )
        securities = response.json().get("securitiesAccount", {})
        return list(securities.get("positions") or [])

    def get_account_snapshot(self) -> dict:
        """Return balances and all positions for startup reconciliation."""
        account = self._require_account()
        response = self._request(
            "GET",
            f"{TRADER_BASE}/accounts/{account}",
            params={"fields": "positions"},
        )
        return dict(response.json().get("securitiesAccount") or {})

    def get_position(self) -> dict | None:
        """Return the selected symbol's position in the live-engine shape."""
        if not self.symbol:
            return None
        for position in self.get_positions():
            instrument = position.get("instrument") or {}
            if str(instrument.get("symbol", "")).upper() != self.symbol:
                continue
            long_qty = float(position.get("longQuantity") or 0)
            short_qty = float(position.get("shortQuantity") or 0)
            qty = long_qty - short_qty
            if qty == 0:
                return None
            return {
                "side": "long" if qty > 0 else "short",
                "contracts": abs(qty),
                "entryPrice": float(position.get("averagePrice") or 0),
                "unrealizedPnl": 0.0,
            }
        return None

    def get_quote(self, symbol: str) -> dict:
        symbol = self.normalize_symbol(symbol)
        response = self._request("GET", f"{MARKET_DATA_BASE}/{symbol}/quotes")
        return response.json()

    def get_quote_price(self, symbol: str) -> float:
        symbol = self.normalize_symbol(symbol)
        payload = self.get_quote(symbol)
        row = payload.get(symbol, payload)
        quote = row.get("quote", row) if isinstance(row, dict) else {}
        for key in ("lastPrice", "mark", "closePrice", "bidPrice", "askPrice"):
            value = quote.get(key)
            if value is not None and float(value) > 0:
                return float(value)
        raise SchwabError(f"Schwab returned no usable price for {symbol}")

    def get_quote_bid_ask(self, symbol: str) -> dict | None:
        """Return a live bid/ask quote for ``symbol`` or ``None`` if unusable.

        Returns ``{"bid": float, "ask": float, "time": datetime | None}``. The
        bid/ask come straight from Schwab's quote payload; ``time`` is the
        quote time (None when only a last/mark price was available). Used by
        the live engine so quote-age and spread limits are enforced against a
        real quote instead of a fabricated one.
        """
        symbol = self.normalize_symbol(symbol)
        payload = self.get_quote(symbol)
        row = payload.get(symbol, payload)
        quote = row.get("quote", row) if isinstance(row, dict) else {}
        bid = _as_positive_float(quote.get("bidPrice"))
        ask = _as_positive_float(quote.get("askPrice"))
        if bid is not None and ask is not None:
            return {"bid": bid, "ask": ask, "time": _parse_quote_time(quote.get("quoteTime"))}
        last = _as_positive_float(quote.get("lastPrice"))
        if last is None:
            last = _as_positive_float(quote.get("mark"))
        if last is not None:
            return {"bid": last, "ask": last, "time": None}
        return None

    def close(self) -> None:
        """Release the underlying requests session (file descriptors /
        connection pool). Call on engine teardown and watchdog restarts so
        rebuilds do not leak sockets over a long-lived process."""
        if self.session is not None:
            try:
                self.session.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                logger.debug("Error closing Schwab session", exc_info=True)
            self.session = None

    def get_option_chain(
        self,
        symbol: str,
        *,
        contract_type: str = "ALL",
        strike_count: int = 20,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict:
        """Return Schwab's current option chain without reshaping away quote fields."""
        symbol = self.normalize_symbol(symbol)
        contract_type = contract_type.upper()
        if contract_type not in {"ALL", "CALL", "PUT"}:
            raise ValueError("contract_type must be ALL, CALL, or PUT")
        params: dict[str, Any] = {
            "symbol": symbol,
            "contractType": contract_type,
            "strikeCount": max(1, min(int(strike_count), 100)),
            "includeUnderlyingQuote": "true",
            "strategy": "SINGLE",
        }
        if int(strike_count) > 100:
            # Schwab caps strikes per expiry at 100 with no pagination; a wider
            # request is silently truncated, so surface it rather than have a
            # consumer build an options surface from a censored chain.
            logger.warning(
                "Schwab option chain is capped at 100 strikes per expiry; "
                "requested %s strikes are silently truncated",
                int(strike_count),
            )
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        return self._request(
            "GET", f"{MARKET_DATA_BASE}/chains", params=params
        ).json()

    def fetch_bars(
        self,
        symbol: str,
        *,
        period_type: str = "day",
        period: int = 10,
        frequency_type: str = "minute",
        frequency: int = 15,
    ) -> list[dict]:
        symbol = self.normalize_symbol(symbol)
        response = self._request(
            "GET",
            f"{MARKET_DATA_BASE}/pricehistory",
            params={
                "symbol": symbol,
                "periodType": period_type,
                "period": period,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "needExtendedHoursData": "false",
            },
        )
        return list(response.json().get("candles") or [])

    @staticmethod
    def _drop_in_progress(rows: list[list], bar_ms: int) -> list[list]:
        """Drop the currently-in-progress bar (mirrors the CCXT data path).

        Exchanges return the forming bar as the last row with partial OHLC;
        letting it through would seed indicators with a half-baked candle, and
        the finalized bar is never re-processed once it closes — a
        backtest-vs-live divergence. The boundary is the START of the forming
        bar: any bar with ts >= (now // bar_ms) * bar_ms is partial.
        """
        now_ms = int(time.time() * 1000)
        current_start_ms = (now_ms // bar_ms) * bar_ms
        return [row for row in rows if int(row[0]) < current_start_ms]

    def fetch_chart_bars(self, symbol: str, timeframe: str) -> list[list]:
        """Return closed candles in the canonical OHLCV row shape.

        The currently-forming (partial) candle is dropped — mirroring the
        CCXT adapter — so warmup never seeds indicators with a half-baked bar
        that silently diverges from backtest.
        """
        if timeframe in {"1m", "5m", "15m", "30m", "1h"}:
            requested_minutes = int(timeframe[:-1]) * (60 if timeframe.endswith("h") else 1)
            base_minutes = 30 if requested_minutes >= 60 else requested_minutes
            candles = self.fetch_bars(
                symbol,
                period_type="day",
                period=10,
                frequency_type="minute",
                frequency=base_minutes,
            )
            rows = [
                [
                    int(c["datetime"]),
                    float(c["open"]),
                    float(c["high"]),
                    float(c["low"]),
                    float(c["close"]),
                    float(c.get("volume") or 0),
                ]
                for c in candles
            ]
            # Drop the forming base-minute bar BEFORE aggregating, so a
            # finished 1h bucket built from a partial 30m candle is excluded.
            rows = self._drop_in_progress(rows, base_minutes * 60_000)
            if requested_minutes == base_minutes:
                return rows
            return self._aggregate_bars(rows, requested_minutes * 60_000)
        if timeframe in {"1d", "1w"}:
            candles = self.fetch_bars(
                symbol,
                period_type="year",
                period=5,
                frequency_type="daily" if timeframe == "1d" else "weekly",
                frequency=1,
            )
            rows = [
                [
                    int(c["datetime"]),
                    float(c["open"]),
                    float(c["high"]),
                    float(c["low"]),
                    float(c["close"]),
                    float(c.get("volume") or 0),
                ]
                for c in candles
            ]
            # Daily bars use UTC-aligned day buckets; weekly bars use Monday
            # 00:00 UTC. Either way the only bar dropped is the one that is
            # still forming (or today's empty-looking partial).
            return self._drop_in_progress(
                rows, 604_800_000 if timeframe == "1w" else 86_400_000
            )
        raise ValueError(f"Unsupported Schwab timeframe: {timeframe}")

    @staticmethod
    def _aggregate_bars(rows: list[list], bucket_ms: int) -> list[list]:
        buckets: dict[int, list[list]] = {}
        for row in rows:
            buckets.setdefault((int(row[0]) // bucket_ms) * bucket_ms, []).append(row)
        return [
            [
                timestamp,
                group[0][1],
                max(row[2] for row in group),
                min(row[3] for row in group),
                group[-1][4],
                sum(row[5] for row in group),
            ]
            for timestamp, group in sorted(buckets.items())
        ]

    @staticmethod
    def _extract_order_id(response: requests.Response) -> str:
        """Pull the broker order id from the Location header of a POST response."""
        location = response.headers.get("Location", "")
        return location.rstrip("/").rsplit("/", 1)[-1] if location else ""

    def _track(self, order_id: str, status: str) -> None:
        with self._orders_lock:
            self._tracked_orders[order_id] = status

    def _tracked_items(self) -> list[tuple[str, str]]:
        with self._orders_lock:
            return list(self._tracked_orders.items())

    def place_order(
        self,
        *,
        symbol: str,
        instruction: str,
        quantity: int | float,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_price: float | None = None,
    ) -> BrokerOrder:
        account = self._require_account()
        symbol = self.normalize_symbol(symbol)
        instruction = instruction.upper()
        if instruction not in {"BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"}:
            raise ValueError(f"Unsupported Schwab instruction: {instruction}")
        if int(quantity) != quantity or quantity <= 0:
            raise ValueError("Schwab equity orders require positive whole shares")
        order_type = order_type.upper()
        if order_type not in {"MARKET", "LIMIT", "STOP"}:
            raise ValueError(f"Unsupported Schwab order type: {order_type}")
        payload: dict[str, Any] = {
            "orderType": order_type,
            "session": "NORMAL",
            "duration": "DAY",
        }
        if order_type == "LIMIT":
            if price is None or price <= 0:
                raise ValueError("limit order requires a positive price")
            payload["price"] = str(price)
        if order_type == "STOP":
            if stop_price is None or stop_price <= 0:
                raise ValueError("stop order requires a positive stop price")
            payload["stopPrice"] = str(stop_price)
        payload.update(
            {
                "orderStrategyType": "SINGLE",
                "orderLegCollection": [
                    {
                        "instruction": instruction,
                        "quantity": int(quantity),
                        "instrument": {"symbol": symbol, "assetType": "EQUITY"},
                    }
                ],
            }
        )
        response = self._request(
            "POST", f"{TRADER_BASE}/accounts/{account}/orders", json=payload
        )
        order_id = self._extract_order_id(response)
        if not order_id:
            raise SchwabAmbiguousOrderError(
                "Schwab accepted an order without returning an order id; trading paused"
            )
        self._track(order_id, "accepted")
        logger.info("Schwab equity order submitted: id=%s %s %s", order_id, instruction, quantity)
        return BrokerOrder(order_id=order_id, status="accepted")

    def place_option_order(
        self,
        *,
        symbol: str,
        instruction: str,
        quantity: int,
        order_type: str = "LIMIT",
        price: float | None = None,
    ) -> BrokerOrder:
        """Submit one OCC option contract order through the selected account."""
        account = self._require_account()
        instruction = instruction.upper()
        if instruction not in {
            "BUY_TO_OPEN",
            "SELL_TO_OPEN",
            "BUY_TO_CLOSE",
            "SELL_TO_CLOSE",
        }:
            raise ValueError(f"Unsupported option instruction: {instruction}")
        if quantity <= 0 or int(quantity) != quantity:
            raise ValueError("option quantity must be a positive whole number")
        order_type = order_type.upper()
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("option order type must be MARKET or LIMIT")
        payload: dict[str, Any] = {
            "orderType": order_type,
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": instruction,
                    "quantity": int(quantity),
                    "instrument": {"symbol": symbol.strip(), "assetType": "OPTION"},
                }
            ],
        }
        if order_type == "LIMIT":
            if price is None or price <= 0:
                raise ValueError("limit option order requires a positive price")
            payload["price"] = str(price)
        response = self._request(
            "POST", f"{TRADER_BASE}/accounts/{account}/orders", json=payload
        )
        order_id = self._extract_order_id(response)
        if not order_id:
            raise SchwabAmbiguousOrderError(
                "Schwab accepted an option order without returning an order id"
            )
        self._track(order_id, "accepted")
        logger.info(
            "Schwab option order submitted: id=%s %s %s",
            order_id,
            instruction,
            quantity,
        )
        return BrokerOrder(order_id=order_id, status="accepted")

    def place_option_strategy(
        self,
        *,
        legs: list[dict],
        net_limit_price: float | None,
    ) -> BrokerOrder:
        """Submit a Schwab-native atomic option combination (two to four legs)."""
        account = self._require_account()
        if not 2 <= len(legs) <= 4:
            raise ValueError("option strategy requires two to four legs")
        normalized = []
        valid_instructions = {
            "BUY_TO_OPEN",
            "SELL_TO_OPEN",
            "BUY_TO_CLOSE",
            "SELL_TO_CLOSE",
        }
        for leg in legs:
            instruction = str(leg["instruction"]).upper()
            quantity = leg["quantity"]
            if instruction not in valid_instructions:
                raise ValueError(f"Unsupported option instruction: {instruction}")
            if quantity <= 0 or int(quantity) != quantity:
                raise ValueError("option leg quantity must be a positive integer")
            normalized.append(
                {
                    "instruction": instruction,
                    "quantity": int(quantity),
                    "instrument": {
                        "symbol": str(leg["symbol"]).strip(),
                        "assetType": "OPTION",
                    },
                }
            )
        if net_limit_price is None or net_limit_price == 0:
            # A market-priced multi-leg option combination has no execution
            # anchor and makes net-debit/credit accounting meaningless; the
            # framework policy is that multi-leg option orders are always
            # submitted atomically at a net limit price.
            raise ValueError(
                "multi-leg option strategy requires a non-zero net limit price"
            )
        # ``net_limit_price`` sign convention: the intent builder
        # (``options/execution.py``) computes it as (credit - debit), so a
        # POSITIVE value is a NET CREDIT to the account. Schwab's orderType
        # must mirror that: positive -> NET_CREDIT, negative -> NET_DEBIT.
        # (This was previously inverted, so every roll order was submitted
        # with the wrong type.)
        order_type = "NET_CREDIT" if net_limit_price > 0 else "NET_DEBIT"
        payload: dict[str, Any] = {
            "orderType": order_type,
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "complexOrderStrategyType": "CUSTOM",
            "orderLegCollection": normalized,
            "price": str(abs(net_limit_price)),
        }
        response = self._request(
            "POST", f"{TRADER_BASE}/accounts/{account}/orders", json=payload
        )
        order_id = self._extract_order_id(response)
        if not order_id:
            raise SchwabAmbiguousOrderError(
                "Schwab accepted a multi-leg order without returning an order id"
            )
        self._track(order_id, "accepted")
        logger.info(
            "Schwab multi-leg order submitted: id=%s %s(%s) legs=%d",
            order_id,
            order_type,
            net_limit_price,
            len(legs),
        )
        return BrokerOrder(order_id=order_id, status="accepted")

    # NOTE: a bare ``submit_market_order`` used to live here as a "compatibility
    # entry point". It was removed: it short-circuited the canonical
    # Strategy → RiskEngine → ExecutionService → Adapter path and was never
    # called by any production code. Real Schwab orders must go through
    # ``SchwabExecutionAdapter``.
    def get_order(self, order_id: str) -> BrokerOrder:

        account = self._require_account()
        response = self._request(
            "GET", f"{TRADER_BASE}/accounts/{account}/orders/{order_id}"
        )
        raw = response.json()
        status = _STATUS_MAP.get(str(raw.get("status", "")).upper(), "unknown")
        self._track(str(order_id), status)
        return BrokerOrder(order_id=str(raw.get("orderId", order_id)), status=status, raw=raw)

    def reconcile_orders(self) -> list[BrokerOrder]:
        results = []
        for order_id, status in self._tracked_items():
            if status in {"filled", "canceled", "rejected"}:
                continue
            order = self.get_order(order_id)
            results.append(order)
            if order.status == "unknown":
                raise SchwabAmbiguousOrderError(
                    f"Schwab order {order_id} has an unknown state; trading paused"
                )
        return results

    def cancel_order(self, order_id: str) -> None:
        account = self._require_account()
        self._request("DELETE", f"{TRADER_BASE}/accounts/{account}/orders/{order_id}")

    def cancel_tracked_orders(self) -> list[str]:
        """Best-effort cancellation for every locally tracked working order."""
        canceled = []
        for order_id, status in self._tracked_items():
            if status in {"filled", "canceled", "rejected"}:
                continue
            self.cancel_order(order_id)
            self._track(order_id, "canceled")
            canceled.append(order_id)
        if canceled:
            logger.warning("Cancelled %d tracked Schwab order(s)", len(canceled))
        return canceled

    def _require_account(self) -> str:
        if not self.account_hash:
            raise SchwabAuthError("Select a Schwab account before trading")
        return self.account_hash


def credentials_for(product: str = "trading") -> SchwabCredentials:
    """Load product credentials from env, falling back to Dynaconf secrets."""
    if product not in {"trading", "market_data"}:
        raise ValueError(f"Unknown Schwab product: {product}")
    prefix = "SCHWAB_TRADING" if product == "trading" else "SCHWAB_MARKET_DATA"
    values = {
        "app_key": os.environ.get(f"{prefix}_APP_KEY")
        or os.environ.get("SCHWAB_APP_KEY", ""),
        "app_secret": os.environ.get(f"{prefix}_APP_SECRET")
        or os.environ.get("SCHWAB_APP_SECRET", ""),
        "callback_url": os.environ.get("SCHWAB_CALLBACK_URL", ""),
    }
    try:
        secrets_path = Path(__file__).resolve().parents[2] / ".keys/.secrets.toml"
        with secrets_path.open("rb") as handle:
            schwab = tomllib.load(handle).get("SCHWAB") or {}
        product_config = schwab.get(product.upper()) or {}
        values["app_key"] = values["app_key"] or product_config.get("CLIENT_ID", "")
        values["app_secret"] = values["app_secret"] or product_config.get(
            "CLIENT_SECRET", ""
        )
        values["callback_url"] = values["callback_url"] or schwab.get(
            "CALLBACK_URL", ""
        )
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError, AttributeError, TypeError):
        logger.warning(
            "Schwab secrets file at .keys/.secrets.toml is missing or unreadable; "
            "falling back to environment configuration"
        )
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SchwabAuthError(
            f"Missing Schwab {product} configuration: " + ", ".join(missing)
        )
    return SchwabCredentials(**values)


def credentials_from_env() -> SchwabCredentials:
    """Backward-compatible alias for trading credentials."""
    return credentials_for("trading")


def selected_account_hash() -> str | None:
    """Load the selected account without exposing its raw account number."""
    env_value = os.environ.get("SCHWAB_ACCOUNT_HASH")
    if env_value:
        return env_value
    path = Path.home() / ".quantforge/schwab/config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("account_hash")
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
