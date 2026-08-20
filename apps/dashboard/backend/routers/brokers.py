"""Broker authentication and account-selection endpoints."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from quantforge.brokers.schwab import (
    SchwabAuthError,
    SchwabConnector,
    SchwabOAuthClient,
    SchwabTokenStore,
    credentials_for,
)
from apps.dashboard.backend.http_errors import safe_exception_detail
from quantforge.brokers.reconciliation import reconcile_schwab_account

router = APIRouter(prefix="/brokers/schwab", tags=["brokers"])
_pending_states: dict[str, tuple[float, str]] = {}
_CONFIG_PATH = Path.home() / ".quantforge/schwab/config.json"
_TOKEN_PATHS = {
    "trading": Path.home() / ".quantforge/schwab/tokens-trading.json",
    "market_data": Path.home() / ".quantforge/schwab/tokens-market-data.json",
}


def _account_ref(account_hash: str) -> str:
    """One-way reference for an account so the raw Schwab account_hash (the
    credential value used to authorize Schwab API calls) never leaves the
    server. The digest is not reversible for a high-entropy server-issued
    hash, so the frontend only ever sees a selection token."""
    return hashlib.sha256(account_hash.encode("utf-8")).hexdigest()[:16]


class AccountSelection(BaseModel):
    account_ref: str


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _save_config(config: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(_CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle)
    _CONFIG_PATH.chmod(0o600)


def _connector() -> SchwabConnector:
    config = _load_config()
    return SchwabConnector(
        credentials_for("trading"),
        market_credentials=credentials_for("market_data"),
        account_hash=config.get("account_hash"),
    )


@router.get("/status")
def status():
    try:
        connector = _connector()
    except SchwabAuthError as exc:
        return {
            "configured": False,
            "authenticated": False,
            "detail": safe_exception_detail(
                exc, prefix="Schwab authentication unavailable"
            ),
        }
    return {
        "configured": True,
        "authenticated": connector.authenticated,
        "trading_authenticated": connector.trading_authenticated,
        "market_data_authenticated": connector.market_data_authenticated,
        "account_selected": bool(connector.account_hash),
    }


def _purge_pending_states(now: float) -> None:
    """Drop expired OAuth handshakes so repeated /auth/start calls cannot grow
    the table without limit (L10). Clears outright if it still exceeds a hard
    cap, which only happens under abnormal traffic."""
    for state, (expires_at, _product) in list(_pending_states.items()):
        if expires_at < now:
            del _pending_states[state]
    if len(_pending_states) > 10_000:
        _pending_states.clear()


@router.get("/auth/start")
def auth_start(product: str = Query("trading")):
    if product not in _TOKEN_PATHS:
        raise HTTPException(status_code=400, detail="Unknown Schwab product")
    _purge_pending_states(time.time())
    try:
        oauth = SchwabOAuthClient(
            credentials_for(product),
            token_store=SchwabTokenStore(_TOKEN_PATHS[product]),
        )
        url, state = oauth.authorization_url()
    except SchwabAuthError as exc:
        raise HTTPException(status_code=503, detail=safe_exception_detail(exc, prefix="Schwab authorization service unavailable")) from exc
    _pending_states[state] = (time.time() + 600, product)
    return {"authorization_url": url, "product": product}


@router.get("/auth/callback")
def auth_callback(code: str = Query(...), state: str = Query(...)):
    expires_at, product = _pending_states.pop(state, (0, ""))
    if expires_at < time.time():
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    try:
        SchwabOAuthClient(
            credentials_for(product),
            token_store=SchwabTokenStore(_TOKEN_PATHS[product]),
        ).exchange_code(code)
    except SchwabAuthError as exc:
        raise HTTPException(status_code=400, detail=safe_exception_detail(exc, prefix="Schwab OAuth exchange failed")) from exc
    return RedirectResponse(url=f"/?schwab={product}-connected", status_code=302)


@router.get("/accounts")
def accounts():
    try:
        return [
            {
                "account_ref": _account_ref(account.account_hash),
                "account_type": account.account_type,
                "display_id": account.display_id,
            }
            for account in _connector().get_accounts()
        ]
    except SchwabAuthError as exc:
        raise HTTPException(status_code=401, detail=safe_exception_detail(exc, prefix="Schwab authentication failed")) from exc


@router.get("/portfolio")
def portfolio():
    """Return reconciled cash, equities, and option positions."""
    try:
        ledger = reconcile_schwab_account(_connector().get_account_snapshot())
    except SchwabAuthError as exc:
        raise HTTPException(status_code=401, detail=safe_exception_detail(exc, prefix="Schwab authentication failed")) from exc
    return {
        "cash": ledger.cash,
        "positions": [
            {
                "symbol": key.symbol,
                "asset_class": key.asset_class.value,
                "venue": key.venue,
                "quantity": position.quantity,
                "average_price": position.average_price,
                "multiplier": position.instrument.multiplier,
            }
            for key, position in sorted(
                ledger.positions.items(), key=lambda item: item[0].symbol
            )
        ],
    }


@router.post("/account")
def select_account(selection: AccountSelection):
    try:
        connector = _connector()
        accounts = connector.get_accounts()
    except SchwabAuthError as exc:
        raise HTTPException(status_code=401, detail=safe_exception_detail(exc, prefix="Schwab authentication failed")) from exc
    for account in accounts:
        if _account_ref(account.account_hash) == selection.account_ref:
            _save_config({"account_hash": account.account_hash})
            return {"selected": True, "display_id": account.display_id}
    raise HTTPException(status_code=400, detail="Unknown Schwab account reference")


def selected_account_hash() -> str | None:
    return _load_config().get("account_hash")
