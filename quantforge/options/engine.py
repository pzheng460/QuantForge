from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from quantforge.options.manager import OptionDecision, OptionManagerInput
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.strategy.api import Strategy, StrategyContext


@dataclass(frozen=True, slots=True)
class OptionsDailyReport:
    strategy: str
    ticker: str
    action: str
    reasons: tuple[str, ...]
    generated_at: str
    data_quality: str
    contract_symbol: str | None = None
    contracts: int = 0
    limit_price: float | None = None


class OptionReportStore:
    """Persist immutable daily option reports for audit and scheduling."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or Path.home() / ".quantforge/options/reports")

    def save(self, report: OptionsDailyReport) -> Path:
        generated = datetime.fromisoformat(report.generated_at)
        stamp = generated.strftime("%Y%m%dT%H%M%S%fZ")
        path = self.root / report.ticker / f"{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(report), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
        return path


class OptionsEventEngine:
    """Execute event-driven option analysis independently of the bar engine."""

    def __init__(self, strategy: Strategy, ledger: PortfolioLedger | None = None):
        self.strategy = strategy
        self.ledger = ledger or PortfolioLedger()

    def analyze(self, event: OptionManagerInput) -> OptionsDailyReport:
        context = StrategyContext(
            portfolio=self.ledger,
            market=event,
            environment="analysis",
        )
        decisions = self.strategy.on_event(context, event)
        if len(decisions) != 1 or not isinstance(decisions[0], OptionDecision):
            raise RuntimeError("option strategy must return exactly one decision")
        decision = decisions[0]
        return OptionsDailyReport(
            strategy=self.strategy.name,
            ticker=event.ticker,
            action=decision.action,
            reasons=decision.reasons,
            generated_at=datetime.now(UTC).isoformat(),
            data_quality="live_market_data",
            contract_symbol=decision.contract_symbol,
            contracts=decision.contracts,
            limit_price=decision.limit_price,
        )
