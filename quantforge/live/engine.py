from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol

from quantforge.domain.instruments import AssetClass, Instrument
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.execution.service import ExecutionService
from quantforge.strategy.bar import Bar, BarStrategy


class BarFeed(Protocol):
    async def warmup(self, bars: int) -> list[Bar]: ...

    async def next_bar(self) -> Bar: ...


class PythonLiveEngine:
    """Run a trusted Python strategy through the canonical risk/execution path."""

    def __init__(
        self,
        *,
        strategy: BarStrategy,
        instrument: Instrument,
        execution: ExecutionService,
        position_size: float,
        leverage: float = 1,
        feed: BarFeed | None = None,
        warmup_bars: int = 500,
    ) -> None:
        if position_size <= 0:
            raise ValueError("position_size must be positive")
        self.strategy = strategy
        self.instrument = instrument
        self.execution = execution
        self.position_size = position_size
        self.leverage = leverage
        self.feed = feed
        self.warmup_bars = warmup_bars
        existing = execution.ledger.quantity(instrument.id)
        self._target = 1 if existing > 0 else (-1 if existing < 0 else 0)
        self._quantity = abs(existing)
        self._running = False
        self._warmup_complete = False

    def process_bar(self, bar: Bar):
        self.strategy.position = self._target
        target = self.strategy.process_bar(bar)
        if target.position == self._target:
            return None
        if self._target and target.position not in {0, self._target}:
            raise RuntimeError("reversal requires an explicit flat target first")
        price = bar.close
        quantity = self._quantity if target.position == 0 else self.position_size / price
        if self.instrument.id.asset_class in {
            AssetClass.EQUITY,
            AssetClass.EQUITY_OPTION,
        }:
            quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("position size is below the minimum tradable quantity")
        side = (
            OrderSide.BUY
            if (target.position > self._target)
            else OrderSide.SELL
        )
        intent = OrderIntent(
            strategy_id=self.strategy.name,
            instrument=self.instrument,
            side=side,
            quantity=quantity,
            reduce_only=target.position == 0,
            quote_bid=price,
            quote_ask=price,
            quote_timestamp=datetime.now(timezone.utc),
            leverage=self.leverage,
        )
        receipt = self.execution.execute(intent)
        self._target = target.position
        self._quantity = 0.0 if target.position == 0 else quantity
        return receipt

    async def start(self) -> None:
        if self.feed is None:
            raise RuntimeError("live engine requires a market-data feed")
        self._running = True
        for bar in await self.feed.warmup(self.warmup_bars):
            self.strategy.process_bar(bar)
        self._warmup_complete = True
        while self._running:
            bar = await self.feed.next_bar()
            self.process_bar(bar)

    async def stop(self) -> None:
        self._running = False
        await asyncio.sleep(0)
