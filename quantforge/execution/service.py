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
        self,
        intent: OrderIntent | MultiLegOrderIntent,
        *,
        fill_price: float | None = None,
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
        if fill_price is not None:
            # Keep the ledger in sync with fills so ledger-driven risk checks
            # (reduce_only netting, option coverage, cash) see the current
            # book mid-session instead of a startup snapshot. Only callers
            # that know an actual/approx fill price (the live engine, which
            # submits market orders at the current quote) pass it; other paths
            # leave the ledger to their own reconciliation (options lifecycle).
            legs = (
                intent.legs if isinstance(intent, MultiLegOrderIntent) else (intent,)
            )
            for leg in legs:
                self.ledger.apply_fill(
                    leg.instrument, leg.side, leg.quantity, fill_price
                )
        receipt = ExecutionReceipt(intent.intent_id, broker_order_id, "submitted")
        self._receipts[intent.intent_id] = receipt
        return receipt
