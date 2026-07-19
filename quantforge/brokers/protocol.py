"""Common broker contract for crypto exchanges and securities brokers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BrokerOrder:
    order_id: str
    status: str
    raw: dict | None = None


@runtime_checkable
class BrokerConnector(Protocol):
    def place_order(
        self,
        *,
        symbol: str,
        instruction: str,
        quantity: int | float,
        order_type: str = "MARKET",
        price: float | None = None,
        stop_price: float | None = None,
    ) -> BrokerOrder: ...

    def cancel_order(self, order_id: str) -> None: ...

    def get_order(self, order_id: str) -> BrokerOrder: ...

    def get_positions(self) -> list[dict]: ...
