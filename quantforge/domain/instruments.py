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

    def __post_init__(self) -> None:
        if not isinstance(self.id, InstrumentId):
            raise ValueError("instrument requires an InstrumentId")
        if self.multiplier is None or self.multiplier <= 0:
            raise ValueError("instrument multiplier must be positive")


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

    def __post_init__(self) -> None:
        # NOTE: explicit parent call — super().__post_init__() crashes on
        # slots dataclasses (CPython quirk with the injected __init__).
        Instrument.__post_init__(self)
        if self.strike is None or self.strike <= 0:
            raise ValueError("option strike must be positive")
        if self.expiration in (date.min, date.max):
            raise ValueError("option expiration must be an actual date")
        if self.right not in (OptionRight.CALL, OptionRight.PUT):
            raise ValueError("option right must be CALL or PUT")


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

    def __post_init__(self) -> None:
        Instrument.__post_init__(self)
        if self.contract_size is None or self.contract_size <= 0:
            raise ValueError("contract size must be positive")
        if self.max_leverage is None or self.max_leverage <= 0:
            raise ValueError("max leverage must be positive")


@dataclass(frozen=True, slots=True)
class CryptoPerpetual(CryptoDerivative):
    """Perpetual swap (no expiry); a dedicated type so asset-class-specific
    risk rules (e.g. max_leverage caps) can be enforced on it."""

    pass


@dataclass(frozen=True, slots=True)
class CryptoFuture(CryptoDerivative):
    expiration: date = date.max

    @classmethod
    def from_symbol(
        cls,
        symbol: str,
        *,
        venue: str,
        expiration: date,
        contract_size: float = 1,
        max_leverage: float = 1,
    ) -> CryptoFuture:
        if ":" not in symbol or "/" not in symbol.split(":", 1)[0]:
            raise ValueError(
                f"crypto future symbol must look like BASE/QUOTE:SETTLE-YYMMDD: {symbol!r}"
            )
        pair, rest = symbol.split(":", 1)
        base, quote = pair.split("/", 1)
        parts = rest.split("-", 1)
        settlement = parts[0]
        if not settlement or not settlement.isalnum():
            raise ValueError(f"invalid settlement currency in crypto future symbol: {rest!r}")
        if len(parts) == 2:
            # The -YYMMDD suffix must at least be well formed; caller-supplied
            # ``expiration`` remains authoritative for the actual settlement.
            suffix = parts[1]
            if len(suffix) != 6 or not suffix.isdigit():
                raise ValueError(
                    "crypto future date suffix must be YYMMDD "
                    f"({symbol!r})"
                )
        return cls(
            id=InstrumentId(symbol, AssetClass.CRYPTO_FUTURE, venue),
            currency=settlement,
            base_currency=base,
            quote_currency=quote,
            settlement_currency=settlement,
            contract_size=contract_size,
            max_leverage=max_leverage,
            expiration=expiration,
        )
