from __future__ import annotations

from dataclasses import dataclass

from quantforge.domain.events import Assignment
from quantforge.domain.instruments import Equity, EquityOption, OptionRight
from quantforge.domain.intents import OrderSide
from quantforge.portfolio.ledger import PortfolioLedger


@dataclass(frozen=True, slots=True)
class ExpirationResult:
    expired_contracts: float
    assignment: Assignment | None


class OptionLifecycle:
    """Apply physical US equity option expiration to the canonical ledger."""

    def expire(
        self,
        contract: EquityOption,
        underlying: Equity,
        spot: float,
        ledger: PortfolioLedger,
    ) -> ExpirationResult:
        contracts = ledger.quantity(contract.id)
        if contracts == 0:
            return ExpirationResult(0, None)
        in_the_money = (
            spot > contract.strike
            if contract.right is OptionRight.CALL
            else spot < contract.strike
        )
        ledger.remove_position(contract.id)
        if not in_the_money:
            return ExpirationResult(contracts, None)
        shares = abs(contracts) * contract.multiplier
        if contract.right is OptionRight.CALL:
            side = OrderSide.BUY if contracts > 0 else OrderSide.SELL
            signed_shares = shares if contracts > 0 else -shares
        else:
            side = OrderSide.SELL if contracts > 0 else OrderSide.BUY
            signed_shares = -shares if contracts > 0 else shares
        ledger.apply_fill(underlying, side, shares, contract.strike)
        return ExpirationResult(
            contracts,
            Assignment(
                option=contract.id,
                underlying=underlying.id,
                option_quantity=contracts,
                share_quantity=signed_shares,
                strike=contract.strike,
                reason="automatic exercise" if contracts > 0 else "assignment",
            ),
        )
