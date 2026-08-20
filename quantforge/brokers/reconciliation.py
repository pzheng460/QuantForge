from __future__ import annotations

import logging
import re
from datetime import date

from quantforge.domain.instruments import (
    AssetClass,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.portfolio.ledger import PortfolioLedger, Position

logger = logging.getLogger(__name__)

# OCC option symbols arrive from Schwab WITHOUT whitespace (e.g.
# "SPY260116C00550000"); the space historically required between root and
# expiration made the regex never match and left every option on the explicit
# expiration/strike fields. Both forms are accepted.
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z.]+)\s*(?P<date>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)


def _option(instrument: dict) -> EquityOption:
    symbol = str(instrument["symbol"])
    match = _OCC_RE.match(symbol)
    underlying_symbol = str(
        instrument.get("underlyingSymbol")
        or (match.group("root") if match else "")
    ).strip()
    if not underlying_symbol:
        raise ValueError(f"Schwab option lacks underlying symbol: {symbol}")
    if match:
        raw_date = match.group("date")
        expiration = date(
            2000 + int(raw_date[:2]),
            int(raw_date[2:4]),
            int(raw_date[4:6]),
        )
        strike = int(match.group("strike")) / 1000
        right = OptionRight.CALL if match.group("right") == "C" else OptionRight.PUT
    else:
        # Non-OCC symbol: fall back to explicit expiration/strike fields; a
        # broker response missing them must raise a clear error rather than a
        # raw KeyError deep inside reconciliation.
        expires = str(instrument.get("expirationDate") or "")
        strike_raw = instrument.get("strikePrice")
        put_call = str(instrument.get("putCall") or "")
        if not expires or strike_raw is None or put_call not in {"CALL", "PUT", "call", "put"}:
            raise ValueError(
                f"Schwab option lacks expiration/strike/type data: {symbol!r}"
            )
        expiration = date.fromisoformat(expires[:10])
        strike = float(strike_raw)
        right = OptionRight(put_call.lower())
    underlying = InstrumentId(
        underlying_symbol, AssetClass.EQUITY, "schwab"
    )
    return EquityOption(
        id=InstrumentId(symbol, AssetClass.EQUITY_OPTION, "schwab"),
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        right=right,
    )


def reconcile_schwab_account(snapshot: dict) -> PortfolioLedger:
    balances = snapshot.get("currentBalances") or {}
    cash = float(
        balances.get("cashAvailableForTrading")
        or balances.get("cashBalance")
        or 0
    )
    ledger = PortfolioLedger(cash={"USD": cash})
    for row in snapshot.get("positions") or []:
        details = row.get("instrument") or {}
        asset_type = str(details.get("assetType", "")).upper()
        if asset_type == "EQUITY":
            symbol = str(details.get("symbol") or "").strip().upper()
            if not symbol:
                # A broker snapshot missing the equity symbol must raise a
                # clear error instead of a raw KeyError mid-reconciliation.
                raise ValueError("Schwab equity position lacks a symbol")
            instrument = Equity(
                InstrumentId(symbol, AssetClass.EQUITY, "schwab")
            )
        elif asset_type == "OPTION":
            instrument = _option(details)
        else:
            # The ledger only models equity + equity-option positions; crypto,
            # fixed income, forex, etc. would silently understate exposure if
            # skipped without a trace (L3).
            logger.warning(
                "Reconciliation skipped unmodeled Schwab position: assetType=%s "
                "symbol=%s (ledger only models EQUITY/OPTION)",
                asset_type,
                details.get("symbol") or "?",
            )
            continue
        quantity = float(row.get("longQuantity") or 0) - float(
            row.get("shortQuantity") or 0
        )
        if quantity:
            ledger.positions[instrument.id] = Position(
                instrument=instrument,
                quantity=quantity,
                average_price=float(row.get("averagePrice") or 0),
            )
    return ledger
