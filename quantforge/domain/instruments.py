from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class AssetClass(StrEnum):
    EQUITY = "equity"
    EQUITY_OPTION = "equity_option"
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERPETUAL = "crypto_perpetual"
    CRYPTO_FUTURE = "crypto_future"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True, slots=True)
class InstrumentId:
    symbol: str
    asset_class: AssetClass
    venue: str


@dataclass(frozen=True, slots=True)
class Instrument:
    id: InstrumentId
    currency: str = "USD"
    multiplier: float = 1


@dataclass(frozen=True, slots=True)
class Equity(Instrument):
    pass


@dataclass(frozen=True, slots=True)
class EquityOption(Instrument):
    underlying: InstrumentId | None = None
    expiration: date = date.max
    strike: float = 0
    right: OptionRight = OptionRight.CALL
    style: str = "american"
    settlement: str = "physical"
    multiplier: float = 100


@dataclass(frozen=True, slots=True)
class CryptoSpot(Instrument):
    base_currency: str = ""
    quote_currency: str = "USDT"


@dataclass(frozen=True, slots=True)
class CryptoDerivative(Instrument):
    base_currency: str = ""
    quote_currency: str = "USDT"
    settlement_currency: str = "USDT"
    contract_size: float = 1
    linear: bool = True
    max_leverage: float = 1

