from __future__ import annotations

from dataclasses import dataclass

from quantforge.domain.instruments import (
    AssetClass,
    EquityOption,
    OptionRight,
)
from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger


class RiskRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RiskLimits:
    live_enabled: bool = False
    halted: bool = False
    max_order_notional: float = 10_000
    max_spread_pct: float = 0.15
    max_option_legs: int = 4


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    intent_id: str


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self._authorized: set[str] = set()

    def authorize(
        self, intent: OrderIntent | MultiLegOrderIntent, ledger: PortfolioLedger
    ) -> RiskDecision:
        if not self.limits.live_enabled:
            raise RiskRejected("live trading is disabled")
        if self.limits.halted:
            raise RiskRejected("risk engine is halted")
        if intent.intent_id in self._authorized:
            raise RiskRejected("duplicate intent")
        legs = intent.legs if isinstance(intent, MultiLegOrderIntent) else (intent,)
        if len(legs) > self.limits.max_option_legs:
            raise RiskRejected("too many option legs")
        for leg in legs:
            self._validate_order(leg, ledger, legs)
        self._authorized.add(intent.intent_id)
        return RiskDecision(True, intent.intent_id)

    def _validate_order(self, order, ledger, plan) -> None:
        if order.quantity <= 0:
            raise RiskRejected("quantity must be positive")
        price = order.limit_price
        if price is None and order.quote_ask is not None:
            price = order.quote_ask
        if price is not None:
            notional = price * order.quantity * order.instrument.multiplier
            if notional > self.limits.max_order_notional:
                raise RiskRejected("maximum order notional exceeded")
        if order.quote_bid is not None and order.quote_ask is not None:
            mid = (order.quote_bid + order.quote_ask) / 2
            if mid <= 0 or order.quote_ask < order.quote_bid:
                raise RiskRejected("invalid quote")
            if (order.quote_ask - order.quote_bid) / mid > self.limits.max_spread_pct:
                raise RiskRejected("spread limit exceeded")
        inst = order.instrument
        if (
            isinstance(inst, EquityOption)
            and inst.right is OptionRight.CALL
            and order.side is OrderSide.SELL
        ):
            long_calls = sum(
                leg.quantity
                for leg in plan
                if isinstance(leg.instrument, EquityOption)
                and leg.instrument.underlying == inst.underlying
                and leg.instrument.right is OptionRight.CALL
                and leg.side is OrderSide.BUY
            )
            shares = ledger.quantity(inst.underlying) if inst.underlying else 0
            if shares + long_calls * inst.multiplier < order.quantity * inst.multiplier:
                raise RiskRejected("naked call is prohibited")
        if inst.id.asset_class is AssetClass.CRYPTO_PERPETUAL:
            max_lev = getattr(inst, "max_leverage", 1)
            if max_lev <= 0:
                raise RiskRejected("invalid leverage limit")
