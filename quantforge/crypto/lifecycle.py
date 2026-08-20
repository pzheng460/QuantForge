from __future__ import annotations

import math
from dataclasses import dataclass

from quantforge.domain.instruments import CryptoFuture
from quantforge.portfolio.ledger import PortfolioLedger


@dataclass(frozen=True, slots=True)
class CryptoSettlementResult:
    contracts: float
    settlement_price: float
    realized_pnl: float


def settle_crypto_future(
    contract: CryptoFuture,
    *,
    settlement_price: float,
    ledger: PortfolioLedger,
) -> CryptoSettlementResult:
    # NaN passes <=, so reject non-finite prices outright: a NaN settlement
    # would poison the ledger's cash with a NaN P&L that no later arithmetic
    # can detect.
    if not math.isfinite(settlement_price) or settlement_price <= 0:
        raise ValueError("settlement price must be a positive finite number")
    position = ledger.positions.get(contract.id)
    if position is None:
        return CryptoSettlementResult(0, settlement_price, 0)
    pnl = (
        (settlement_price - position.average_price)
        * position.quantity
        * contract.contract_size
    )
    ledger.cash[contract.settlement_currency] = (
        ledger.cash.get(contract.settlement_currency, 0) + pnl
    )
    ledger.remove_position(contract.id)
    return CryptoSettlementResult(position.quantity, settlement_price, pnl)
