"""Broker authentication and account-selection endpoints."""

from __future__ import annotations

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

router = APIRouter(prefix="/brokers/schwab", tags=["brokers"])
_pending_states: dict[str, tuple[float, str]] = {}
_CONFIG_PATH = Path.home() / ".quantforge/schwab/config.json"
_TOKEN_PATHS = {
    "trading": Path.home() / ".quantforge/schwab/tokens-trading.json",
    "market_data": Path.home() / ".quantforge/schwab/tokens-market-data.json",
}


class AccountSelection(BaseModel):
    account_hash: str


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
        return {"configured": False, "authenticated": False, "detail": str(exc)}
    return {
        "configured": True,
        "authenticated": connector.authenticated,
        "trading_authenticated": connector.trading_authenticated,
        "market_data_authenticated": connector.market_data_authenticated,
        "account_selected": bool(connector.account_hash),
    }


@router.get("/auth/start")
def auth_start(product: str = Query("trading")):
    if product not in _TOKEN_PATHS:
        raise HTTPException(status_code=400, detail="Unknown Schwab product")
    try:
        oauth = SchwabOAuthClient(
            credentials_for(product),
            token_store=SchwabTokenStore(_TOKEN_PATHS[product]),
        )
        url, state = oauth.authorization_url()
    except SchwabAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/?schwab={product}-connected", status_code=302)


@router.get("/accounts")
def accounts():
    try:
        return [account.__dict__ for account in _connector().get_accounts()]
    except SchwabAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/account")
def select_account(selection: AccountSelection):
    try:
        connector = _connector()
        valid_hashes = {account.account_hash for account in connector.get_accounts()}
    except SchwabAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if selection.account_hash not in valid_hashes:
        raise HTTPException(status_code=400, detail="Unknown Schwab account hash")
    _save_config({"account_hash": selection.account_hash})
    return {"selected": True, "account_hash": selection.account_hash}


def selected_account_hash() -> str | None:
    return _load_config().get("account_hash")
