from __future__ import annotations

from dataclasses import dataclass, field

from quantforge.domain.instruments import Instrument, InstrumentId
from quantforge.domain.intents import OrderSide


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
        if quantity <= 0 or price < 0:
            raise ValueError("fill quantity must be positive and price non-negative")
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
        self.cash[instrument.currency] = self.cash.get(instrument.currency, 0) - (
            signed * price * instrument.multiplier
        )
        if new_qty == 0:
            self.remove_position(instrument.id)
