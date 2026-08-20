from __future__ import annotations

import math
from dataclasses import dataclass, field

from quantforge.domain.instruments import Instrument, InstrumentId
from quantforge.domain.intents import OrderSide


class InsufficientCash(RuntimeError):
    """Raised when a BUY fill cannot be afforded from the cash pool.

    Applies to cash (non-margin) instruments. Margin instruments
    (``max_leverage > 1``) are allowed to draw the cash pool down, matching
    the framework's margin model.
    """


@dataclass(slots=True)
class Position:
    instrument: Instrument
    quantity: float = 0
    average_price: float = 0


@dataclass(slots=True)
class PortfolioLedger:
    cash: dict[str, float] = field(default_factory=dict)
    positions: dict[InstrumentId, Position] = field(default_factory=dict)

    def quantity(self, instrument_id: InstrumentId) -> float:
        position = self.positions.get(instrument_id)
        return position.quantity if position else 0

    def remove_position(self, instrument_id: InstrumentId) -> Position | None:
        return self.positions.pop(instrument_id, None)

    def apply_fill(
        self, instrument: Instrument, side: OrderSide, quantity: float, price: float
    ) -> None:
        if (
            quantity <= 0
            or price < 0
            or not math.isfinite(quantity)
            or not math.isfinite(price)
        ):
            raise ValueError(
                "fill quantity must be a positive finite number and "
                "price a non-negative finite number"
            )
        signed = quantity if side is OrderSide.BUY else -quantity
        current = self.positions.get(instrument.id)
        old_qty = current.quantity if current else 0
        new_qty = old_qty + signed
        if current is None:
            current = Position(instrument)
            self.positions[instrument.id] = current
        if old_qty == 0 or (old_qty > 0) == (signed > 0):
            current.average_price = (
                abs(old_qty) * current.average_price + quantity * price
            ) / abs(new_qty)
        elif new_qty != 0 and (old_qty > 0) != (new_qty > 0):
            current.average_price = price
        current.quantity = new_qty
        # Contract multiplier: crypto derivatives carry ``contract_size``
        # (from ccxt ``contractSize``); reuse the SAME field the settlement
        # path uses so the fill notional and settlement P&L can never
        # disagree (the old code debited ``multiplier`` but settled on
        # ``contract_size`` for contract_size != multiplier instruments).
        multiplier = getattr(instrument, "contract_size", None) or instrument.multiplier
        debit = signed * price * multiplier
        # Cash pool key: crypto derivatives settle in their settlement
        # currency (e.g. USDT) — key the debit exactly like
        # ``settle_crypto_future`` does, or fills and settlements silently
        # touch different cash buckets (the old code used the default USD
        # currency for exchange-traded crypto).
        currency = (
            getattr(instrument, "settlement_currency", None) or instrument.currency
        )
        balance = self.cash.get(currency, 0)
        # max_leverage should have been validated at instrument construction
        # (positive finite), but defense-in-depth: a NaN/0/negative value must
        # FAIL CLOSED toward the cash guard (treat as non-margin), never
        # silently grant margin privileges.
        leverage = getattr(instrument, "max_leverage", 1)
        if not isinstance(leverage, (int, float)) or not math.isfinite(leverage) or leverage <= 0:
            leverage = 1
        if side is OrderSide.BUY and leverage <= 1 and debit > balance:
            raise InsufficientCash(
                f"fill of {quantity:g} {instrument.id} at {price:g} needs "
                f"{debit:.2f} {currency}; ledger has "
                f"{balance:.2f} available"
            )
        self.cash[currency] = balance - debit
        if new_qty == 0:
            self.remove_position(instrument.id)
