from __future__ import annotations

from dataclasses import dataclass

import pytest

from quantforge.domain.instruments import AssetClass, CryptoDerivative, InstrumentId
from quantforge.execution.service import ExecutionService
from quantforge.live.engine import PythonLiveEngine
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected
from quantforge.strategy.bar import Bar, BarStrategy, PositionTarget


class ToggleStrategy(BarStrategy):
    name = "toggle"

    def setup(self) -> None:
        pass

    def on_bar(self, bar: Bar) -> PositionTarget:
        self.position = 1 if bar.close >= 100 else 0
        return PositionTarget(self.position)


@dataclass
class RecordingAdapter:
    intents: list

    def submit(self, intent):
        self.intents.append(intent)
        return f"order-{len(self.intents)}"


def _engine(*, live_enabled: bool = True):
    instrument = CryptoDerivative(
        id=InstrumentId(
            "BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "bitget"
        ),
        max_leverage=3,
    )
    ledger = PortfolioLedger(cash={"USDT": 10_000})
    adapter = RecordingAdapter([])
    execution = ExecutionService(
        risk=RiskEngine(
            RiskLimits(
                live_enabled=live_enabled,
                max_order_notional=1_000,
                require_fresh_quote=True,
            )
        ),
        ledger=ledger,
        adapter=adapter,
    )
    strategy = ToggleStrategy(ToggleStrategy.config_model())
    return (
        PythonLiveEngine(
            strategy=strategy,
            instrument=instrument,
            execution=execution,
            position_size=500,
            leverage=2,
        ),
        adapter,
    )


def test_python_live_engine_only_orders_when_target_changes():
    engine, adapter = _engine()

    engine.process_bar(Bar(1, 100, 101, 99, 100, 10))
    engine.process_bar(Bar(2, 101, 102, 100, 101, 10))
    engine.process_bar(Bar(3, 99, 100, 98, 99, 10))

    assert [intent.side.value for intent in adapter.intents] == ["buy", "sell"]
    assert adapter.intents[0].leverage == 2
    assert adapter.intents[1].reduce_only is True


def test_python_live_engine_cannot_bypass_global_live_switch():
    engine, _adapter = _engine(live_enabled=False)

    with pytest.raises(RiskRejected, match="disabled"):
        engine.process_bar(Bar(1, 100, 101, 99, 100, 10))
