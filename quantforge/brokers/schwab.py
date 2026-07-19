"""Native Charles Schwab Trader and Market Data API connector."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from quantforge.brokers.protocol import BrokerOrder

logger = logging.getLogger(__name__)

AUTH_BASE = "https://api.schwabapi.com/v1/oauth"
TRADER_BASE = "https://api.schwabapi.com/trader/v1"
MARKET_DATA_BASE = "https://api.schwabapi.com/marketdata/v1"


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
                timeout=30.0,
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
        account_hash: str | None = None,
        symbol: str | None = None,
        token_path: str | Path | None = None,
        session=None,
        access_token: str | None = None,
    ) -> None:
        self.credentials = credentials
        self.account_hash = account_hash
        self.symbol = self.normalize_symbol(symbol) if symbol else None
        self.session = session or requests.Session()
        self.token_store = SchwabTokenStore(token_path)
        self.oauth = SchwabOAuthClient(
            credentials, token_store=self.token_store, session=self.session
        )
        self._token = self.token_store.load() or {}
        self._tracked_orders: dict[str, str] = {}
        if access_token:
            self._token = {"access_token": access_token, "expires_in": 1800}

    @property
    def authenticated(self) -> bool:
        return bool(self._token.get("access_token"))

    def _access_token(self) -> str:
        token = self._token
        obtained = float(token.get("obtained_at", time.time()))
        expires = float(token.get("expires_in", 1800))
        if time.time() >= obtained + expires - 60:
            refresh_token = token.get("refresh_token")
            if not refresh_token:
                raise SchwabAuthError("Schwab authorization is required")
            token = self.oauth.refresh(str(refresh_token))
            if not token.get("refresh_token"):
                token["refresh_token"] = refresh_token
                self.token_store.save(token)
            self._token = token
        value = token.get("access_token")
        if not value:
            raise SchwabAuthError("Schwab authorization is required")
        return str(value)

    def _request(self, method: str, url: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": "application/json",
        }
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        try:
            response = None
            attempts = 3 if method == "GET" else 1
            for attempt in range(attempts):
                response = self.session.request(
                    method, url, headers=headers, timeout=30.0, **kwargs
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt + 1 < attempts:
                    delay = min(float(response.headers.get("Retry-After", 2**attempt)), 5)
                    time.sleep(delay)
            assert response is not None
        except requests.Timeout as exc:
            if method == "POST" and url.endswith("/orders"):
                raise SchwabAmbiguousOrderError(
                    "Schwab order outcome is unknown after timeout; trading paused"
                ) from exc
            raise SchwabError("Schwab request timed out") from exc
        except requests.RequestException as exc:
            raise SchwabError("Schwab request failed") from exc
        if response.status_code in (401, 403):
            raise SchwabAuthError(
                f"Schwab authorization failed (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            error_type = SchwabOrderError if "/orders" in url else SchwabError
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
            qty = long_qty or short_qty
            if qty <= 0:
                return None
            return {
                "side": "long" if long_qty > 0 else "short",
                "contracts": qty,
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

    def fetch_chart_bars(self, symbol: str, timeframe: str) -> list[list]:
        """Return closed candles in the OHLCV row shape used by Pine."""
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
            return [
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
        location = response.headers.get("Location", "")
        order_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        if not order_id:
            raise SchwabAmbiguousOrderError(
                "Schwab accepted an order without returning an order id; trading paused"
            )
        self._tracked_orders[order_id] = "accepted"
        return BrokerOrder(order_id=order_id, status="accepted")

    def submit_market_order(
        self, side: str, qty: float, reduce_only: bool = False
    ) -> dict:
        """Compatibility entry point used by the current Pine OrderBridge."""
        if not self.symbol:
            raise ValueError("Schwab connector requires a symbol for live orders")
        instruction = {
            ("buy", False): "BUY",
            ("sell", False): "SELL_SHORT",
            ("buy", True): "BUY_TO_COVER",
            ("sell", True): "SELL",
        }.get((side.lower(), reduce_only))
        if instruction is None:
            raise ValueError(f"Unsupported side: {side}")
        order = self.place_order(
            symbol=self.symbol,
            instruction=instruction,
            quantity=math.floor(qty),
        )
        return {"id": order.order_id, "status": order.status}

    def get_order(self, order_id: str) -> BrokerOrder:
        account = self._require_account()
        response = self._request(
            "GET", f"{TRADER_BASE}/accounts/{account}/orders/{order_id}"
        )
        raw = response.json()
        status = _STATUS_MAP.get(str(raw.get("status", "")).upper(), "unknown")
        self._tracked_orders[str(order_id)] = status
        return BrokerOrder(order_id=str(raw.get("orderId", order_id)), status=status, raw=raw)

    def reconcile_orders(self) -> list[BrokerOrder]:
        results = []
        for order_id, status in list(self._tracked_orders.items()):
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

    def _require_account(self) -> str:
        if not self.account_hash:
            raise SchwabAuthError("Select a Schwab account before trading")
        return self.account_hash


def credentials_from_env() -> SchwabCredentials:
    values = {
        "app_key": os.environ.get("SCHWAB_APP_KEY", ""),
        "app_secret": os.environ.get("SCHWAB_APP_SECRET", ""),
        "callback_url": os.environ.get("SCHWAB_CALLBACK_URL", ""),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SchwabAuthError("Missing Schwab configuration: " + ", ".join(missing))
    return SchwabCredentials(**values)


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
