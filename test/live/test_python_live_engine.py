from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, CryptoDerivative, InstrumentId
from quantforge.execution.service import ExecutionService
from quantforge.live.engine import LiveQuote, PythonLiveEngine
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


class FlipStrategy(BarStrategy):
    """Returns ±1 directly — no explicit flat target between flips."""

    name = "flip"

    def setup(self) -> None:
        pass

    def on_bar(self, bar: Bar) -> PositionTarget:
        return PositionTarget(1 if bar.close >= 100 else -1)


@dataclass
class RecordingAdapter:
    intents: list

    def submit(self, intent):
        self.intents.append(intent)
        return f"order-{len(self.intents)}"


def _fresh_quote() -> LiveQuote:
    return LiveQuote(bid=100.0, ask=100.0, timestamp=datetime.now(timezone.utc))


def _engine(*, live_enabled: bool = True, quote_provider=None):
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
            quote_provider=quote_provider,
        ),
        adapter,
    )


def test_python_live_engine_only_orders_when_target_changes():
    engine, adapter = _engine(quote_provider=_fresh_quote)

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


def test_live_engine_uses_real_quote_when_provider_available():
    quote_time = datetime.now(timezone.utc)
    engine, adapter = _engine(
        quote_provider=lambda: LiveQuote(bid=99.5, ask=100.5, timestamp=quote_time)
    )

    engine.process_bar(Bar(1, 100, 101, 99, 100, 10))

    assert len(adapter.intents) == 1
    intent = adapter.intents[0]
    assert intent.quote_bid == 99.5
    assert intent.quote_ask == 100.5
    assert intent.quote_timestamp == quote_time


def test_live_engine_missing_quote_fails_closed_under_fresh_quote():
    """Without a usable quote the fallback carries NO timestamp, so under
    require_fresh_quote the risk engine rejects the order — never a fabricated
    fresh zero-spread quote (previously quote_timestamp was set to now)."""
    engine, adapter = _engine(quote_provider=lambda: None)

    with pytest.raises(RiskRejected, match="fresh quote is required"):
        engine.process_bar(Bar(1, 100, 101, 99, 100, 10))
    assert adapter.intents == []  # nothing was ever submitted


def test_live_engine_quote_provider_failure_fails_closed():
    def boom() -> LiveQuote | None:
        raise RuntimeError("quote feed down")

    engine, adapter = _engine(quote_provider=boom)

    with pytest.raises(RiskRejected, match="fresh quote is required"):
        engine.process_bar(Bar(1, 100, 101, 99, 100, 10))
    assert adapter.intents == []


def test_live_engine_approximate_quote_passes_when_fresh_not_required():
    """Without require_fresh_quote the bar-close estimate is usable (e.g. for
    paper/tests), but still carries no timestamp."""
    from dataclasses import replace

    engine, adapter = _engine(quote_provider=lambda: None)
    engine.execution.risk.limits = replace(
        engine.execution.risk.limits, require_fresh_quote=False
    )

    engine.process_bar(Bar(1, 100, 101, 99, 100, 10))

    intent = adapter.intents[0]
    assert intent.quote_bid == 100
    assert intent.quote_ask == 100
    assert intent.quote_timestamp is None


def _flip_engine(*, quote_provider=None):
    instrument = CryptoDerivative(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "bitget"),
        max_leverage=3,
    )
    ledger = PortfolioLedger(cash={"USDT": 10_000})
    adapter = RecordingAdapter([])
    execution = ExecutionService(
        risk=RiskEngine(
            RiskLimits(
                live_enabled=True,
                max_order_notional=1_000,
                require_fresh_quote=True,
            )
        ),
        ledger=ledger,
        adapter=adapter,
    )
    strategy = FlipStrategy(FlipStrategy.config_model())
    return (
        PythonLiveEngine(
            strategy=strategy,
            instrument=instrument,
            execution=execution,
            position_size=500,
            leverage=2,
            quote_provider=quote_provider,
        ),
        adapter,
    )


def test_live_engine_direct_reversal_closes_then_opens():
    """A strategy flipping +1 → -1 in one decision must submit a reduce-only
    close BEFORE the opposite open — matching the shared backtest engine's
    next-bar close-then-open semantic, not crash the live loop (the old
    RuntimeError 'reversal requires an explicit flat target first')."""
    engine, adapter = _flip_engine(quote_provider=_fresh_quote)

    engine.process_bar(Bar(1, 100, 101, 99, 100, 10))  # target +1 → open long
    engine.process_bar(Bar(2, 95, 96, 94, 95, 10))  # target -1 → reversal

    sides = [intent.side.value for intent in adapter.intents]
    assert sides == ["buy", "sell", "sell"]
    assert adapter.intents[1].reduce_only is True  # close the long
    assert adapter.intents[2].reduce_only is False  # open the short
    assert engine._target == -1
    assert engine._quantity > 0


def test_live_engine_reverse_reversal_closes_then_opens():
    """Short → long uses BUY-to-cover for the reduce-only close, then a plain
    BUY open."""
    engine, adapter = _flip_engine(quote_provider=_fresh_quote)

    engine.process_bar(Bar(1, 95, 96, 94, 95, 10))  # target -1 → open short
    engine.process_bar(Bar(2, 100, 101, 99, 100, 10))  # target +1 → reversal

    sides = [intent.side.value for intent in adapter.intents]
    assert sides == ["sell", "buy", "buy"]
    assert adapter.intents[1].reduce_only is True
    assert adapter.intents[2].reduce_only is False
    assert engine._target == 1


def test_live_engine_reversal_close_rejected_aborts_open():
    """If the reduce-only close is refused by a risk gate, the opposite open
    must NEVER be submitted — no stacking on an unconfirmed position."""
    calls = {"n": 0}

    def provider() -> LiveQuote | None:
        calls["n"] += 1
        return _fresh_quote() if calls["n"] == 1 else None

    engine, adapter = _flip_engine(quote_provider=provider)

    engine.process_bar(Bar(1, 100, 101, 99, 100, 10))  # open long (fresh quote)
    with pytest.raises(RiskRejected, match="fresh quote is required"):
        engine.process_bar(Bar(2, 95, 96, 94, 95, 10))  # close blocked at risk

    assert len(adapter.intents) == 1  # only the original open
    assert engine._target == 1  # still long — reversal never started
    assert engine._quantity > 0


def test_live_engine_survives_risk_rejections_without_crashing():
    """A per-bar risk rejection (e.g. stale market) must skip the trade, not
    kill the loop into a watchdog restart cycle."""

    class _Feed:
        def __init__(self, bars):
            self._bars = list(bars)

        async def warmup(self, bars):
            return []

        async def next_bar(self):
            if not self._bars:
                raise RuntimeError("feed exhausted")
            return self._bars.pop(0)

    engine, adapter = _engine(quote_provider=lambda: None)  # every bar rejected
    engine.feed = _Feed([Bar(1, 100, 101, 99, 100, 10), Bar(2, 100, 101, 99, 100, 10)])

    async def run():
        with pytest.raises(RuntimeError, match="feed exhausted"):
            await engine.start()

    asyncio.run(run())
    # Both decision bars were skipped by the risk gate; the engine stayed
    # alive until its feed naturally ended.
    assert adapter.intents == []
