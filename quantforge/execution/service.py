from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.engine import RiskEngine


class ExecutionAdapter(Protocol):
    def submit(self, intent: OrderIntent | MultiLegOrderIntent) -> str: ...


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    intent_id: str
    broker_order_id: str
    status: str


class SubmissionOutcomeUnknown(RuntimeError):
    """Broker may have accepted the order; never retry automatically."""


class ExecutionService:
    """The only route from strategy intent to a live broker adapter."""

    def __init__(
        self,
        *,
        risk: RiskEngine,
        ledger: PortfolioLedger,
        adapter: ExecutionAdapter,
    ):
        self.risk = risk
        self.ledger = ledger
        self.adapter = adapter
        self._receipts: dict[str, ExecutionReceipt] = {}

    def execute(
        self, intent: OrderIntent | MultiLegOrderIntent
    ) -> ExecutionReceipt:
        existing = self._receipts.get(intent.intent_id)
        if existing:
            return existing
        self.risk.authorize(intent, self.ledger)
        try:
            broker_order_id = self.adapter.submit(intent)
        except SubmissionOutcomeUnknown:
            raise
        except Exception:
            self.risk.release(intent)
            raise
        receipt = ExecutionReceipt(intent.intent_id, broker_order_id, "submitted")
        self._receipts[intent.intent_id] = receipt
        return receipt
