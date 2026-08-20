from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from quantforge.domain.instruments import AssetClass, Instrument
from quantforge.domain.intents import OrderIntent, OrderSide, OrderType
from quantforge.execution.service import ExecutionService
from quantforge.risk import RiskRejected
from quantforge.strategy.bar import Bar, BarStrategy

logger = logging.getLogger(__name__)


class BarFeed(Protocol):
    async def warmup(self, bars: int) -> list[Bar]: ...

    async def next_bar(self) -> Bar: ...


@dataclass(frozen=True, slots=True)
class LiveQuote:
    """A real market quote so bid/ask spread and quote-age limits are real.

    ``timestamp`` is the exchange quote time, or ``None`` when the quote is
    approximate (unknown age). A ``None`` timestamp must fail a freshness
    check under ``require_fresh_quote`` instead of masquerading as fresh.
    """

    bid: float
    ask: float
    timestamp: datetime | None


#: Returns a real quote, or None when none is available right now (the engine
#: then falls back to an explicitly approximate bar-close estimate).
QuoteProvider = Callable[[], LiveQuote | None]


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
        quote_provider: QuoteProvider | None = None,
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
        self.quote_provider = quote_provider
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
        # The strategy just made its decision on a completed bar; submit an
        # immediate MARKET order for the current price. This is the live
        # analogue of the backtester's next-bar-open fill: it is causal
        # (built only from already-known data) and intentionally not a
        # limit priced off the same close (which would claim a fill that was
        # never guaranteed).
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
        # Prefer a real market quote when a provider is wired in; fall back to
        # an explicitly approximate bar-close mid otherwise. An approximate
        # quote must NOT be mistaken for tradable data: venues enforcing fresh
        # quotes/spread should wire a quote_provider that returns None when no
        # usable quote exists so risk checks fail closed instead.
        quote: LiveQuote | None = None
        if self.quote_provider is not None:
            try:
                quote = self.quote_provider()
            except Exception:  # noqa: BLE001 — best-effort, never crash on quote
                logger.warning(
                    "Live quote provider failed for %s; using bar-close estimate",
                    self.instrument.id,
                    exc_info=True,
                )
        if quote is None:
            # Approximate bar-close estimate. It carries NO market timestamp:
            # under require_fresh_quote the risk engine rejects it, so a stale
            # or absent market can never slip through the freshness/spread
            # gates as a "fresh, zero-spread" quote.
            quote = LiveQuote(bid=bar.close, ask=bar.close, timestamp=None)
        intent = OrderIntent(
            strategy_id=self.strategy.name,
            instrument=self.instrument,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            reduce_only=target.position == 0,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_timestamp=quote.timestamp,
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
            try:
                self.process_bar(bar)
            except RiskRejected as exc:
                # A risk gate (missing/stale quote, spread, notional, daily
                # cap, ...) refused this bar's order. That is a skip, not a
                # crash: stay alive and re-evaluate on the next bar when
                # conditions (e.g. a fresh quote) may have recovered.
                logger.warning(
                    "Order decision skipped by risk engine for %s: %s",
                    self.instrument.id,
                    exc,
                )
            except Exception:
                logger.exception("Live engine bar processing failed for %s", self.instrument.id)
                raise

    async def stop(self) -> None:
        self._running = False
        await asyncio.sleep(0)
