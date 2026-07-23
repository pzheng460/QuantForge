from __future__ import annotations

from uuid import uuid4

from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent


class PaperExecutionAdapter:
    """Local deterministic adapter; it never contacts a broker."""

    def __init__(self) -> None:
        self.orders: list[OrderIntent | MultiLegOrderIntent] = []

    def submit(self, intent: OrderIntent | MultiLegOrderIntent) -> str:
        self.orders.append(intent)
        return f"paper-{uuid4().hex}"
