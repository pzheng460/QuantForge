from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from quantforge.domain.instruments import AssetClass, Instrument
from quantforge.domain.intents import OrderIntent, OrderSide, OrderType
from quantforge.execution.service import ExecutionReceipt, ExecutionService
from quantforge.portfolio.ledger import InsufficientCash
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
        # Active risk-exit state adopted from PositionTarget (stop_price /
        # trailing_distance), mirroring the backtester's active_stop /
        # active_trailing / trail_anchor semantics.
        self._active_stop: float | None = None
        self._active_trailing: float | None = None
        self._trail_anchor: float | None = None
        self._running = False
        self._warmup_complete = False

    def _resolve_quote(self, bar: Bar) -> LiveQuote:
        """Prefer a real market quote when a provider is wired in; fall back to
        an explicitly approximate bar-close mid otherwise. An approximate
        quote must NOT be mistaken for tradable data: venues enforcing fresh
        quotes/spread should wire a quote_provider that returns None when no
        usable quote exists so risk checks fail closed instead.
        """
        if self.quote_provider is not None:
            try:
                quote = self.quote_provider()
            except Exception:  # noqa: BLE001 — best-effort, never crash on quote
                logger.warning(
                    "Live quote provider failed for %s; using bar-close estimate",
                    self.instrument.id,
                    exc_info=True,
                )
                quote = None
            if quote is not None:
                return quote
        # Approximate bar-close estimate. It carries NO market timestamp:
        # under require_fresh_quote the risk engine rejects it, so a stale
        # or absent market can never slip through the freshness/spread
        # gates as a "fresh, zero-spread" quote.
        return LiveQuote(bid=bar.close, ask=bar.close, timestamp=None)

    def _open_quantity(self, price: float) -> float:
        """Position quantity for a fresh ``position_size`` notional at ``price``."""
        quantity = self.position_size / price
        if self.instrument.id.asset_class in {
            AssetClass.EQUITY,
            AssetClass.EQUITY_OPTION,
        }:
            quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("position size is below the minimum tradable quantity")
        return quantity

    def _submit_order(
        self,
        *,
        target: int,
        close: bool,
        quantity: float,
        price: float,
        quote: LiveQuote,
    ) -> ExecutionReceipt:
        """Submit one MARKET order for the current price.

        ``close=True`` flattens the tracked position with a reduce-only order
        (side derived from the CURRENT position ``self._target``); ``close=False``
        opens/moves toward strategy target ``target``.

        This is the live analogue of the backtester's next-bar-open fill: it is
        causal (built only from already-known data) and intentionally not a
        limit priced off the same close (which would claim a fill that was
        never guaranteed).
        """
        if close:
            side = OrderSide.SELL if self._target > 0 else OrderSide.BUY
        else:
            side = OrderSide.BUY if target > 0 else OrderSide.SELL
        if self.instrument.id.asset_class in {
            AssetClass.EQUITY,
            AssetClass.EQUITY_OPTION,
        }:
            quantity = int(quantity)
        intent = OrderIntent(
            strategy_id=self.strategy.name,
            instrument=self.instrument,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            reduce_only=close,
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_timestamp=quote.timestamp,
            leverage=self.leverage,
        )
        return self.execution.execute(intent, fill_price=price)

    def _evaluate_stops(self, bar: Bar) -> ExecutionReceipt | None:
        """Evaluate the active stop/trailing against the current bar's range.

        Mirrors the backtester: for a long, the trail anchor is raised to the
        bar high (long) / lowered to the bar low (short) FIRST, then the
        opposite extreme is tested against the stop. A triggered stop closes
        the tracked position with a reduce-only market order on this bar and
        clears the active risk-exit state.
        """
        if not self._target or not self._quantity:
            return None
        if self._active_stop is None and self._active_trailing is None:
            return None
        quote = self._resolve_quote(bar)
        fill: float | None = None
        if self._target > 0:
            if self._active_trailing:
                self._trail_anchor = max(self._trail_anchor or bar.high, bar.high)
                stop = self._trail_anchor - self._active_trailing
                self._active_stop = (
                    max(self._active_stop, stop)
                    if self._active_stop is not None
                    else stop
                )
            if self._active_stop is not None and bar.low <= self._active_stop:
                fill = min(bar.open, self._active_stop)
        else:
            if self._active_trailing:
                self._trail_anchor = min(self._trail_anchor or bar.low, bar.low)
                stop = self._trail_anchor + self._active_trailing
                self._active_stop = (
                    min(self._active_stop, stop)
                    if self._active_stop is not None
                    else stop
                )
            if self._active_stop is not None and bar.high >= self._active_stop:
                fill = max(bar.open, self._active_stop)
        if fill is None:
            return None
        receipt = self._submit_order(
            target=0,
            close=True,
            quantity=self._quantity,
            price=fill,
            quote=quote,
        )
        self._target = 0
        self._quantity = 0.0
        self._active_stop = None
        self._active_trailing = None
        self._trail_anchor = None
        return receipt

    def process_bar(self, bar: Bar) -> ExecutionReceipt | None:
        # Risk exits first, mirroring the backtester: a stop/trailing is
        # evaluated against the current bar's range BEFORE the strategy
        # decision, and a triggered stop never re-opens on the same bar.
        stop_receipt = self._evaluate_stops(bar)
        self.strategy.position = self._target
        target = self.strategy.process_bar(bar)
        if stop_receipt is not None:
            return stop_receipt
        if target.position == self._target and not target.has_risk_order:
            # No change and no new risk order: keep the active stop (parity
            # with the backtester, where an unchanged position without a risk
            # order leaves active_stop/active_trailing in place).
            return None

        # A decision that changes the position OR carries a risk order adopts
        # the target's (possibly cleared) stop/trailing fields — mirroring the
        # backtester's pending-application step.
        if target.clear_risk_exits:
            self._active_stop = None
            self._active_trailing = None
            self._trail_anchor = None
        else:
            self._active_stop = target.stop_price
            self._active_trailing = target.trailing_distance
            self._trail_anchor = None

        if target.position == self._target:
            # Position unchanged — the risk-order update was applied above and
            # there is nothing to trade this bar.
            return None

        price = bar.close
        quote = self._resolve_quote(bar)
        last_receipt: ExecutionReceipt | None = None

        reversal = self._target and target.position not in {0, self._target}
        closing_to_flat = self._target != 0 and target.position == 0
        if reversal or closing_to_flat:
            # Any reduction goes first as a reduce-only order. A direct
            # reversal (1 → -1 or -1 → 1) closes the tracked position BEFORE
            # opening the new side — mirroring the shared backtest engine's
            # next-bar semantic (close at the next open, then open), so a
            # strategy that flips side in one decision behaves identically
            # live and in backtest.
            #
            # If this close is rejected by a risk gate, the open is never
            # attempted (never stack on an unconfirmed position); if the open
            # is rejected instead, the tracked state is left flat and the
            # next bar re-evaluates.
            last_receipt = self._submit_order(
                target=target.position,
                close=True,
                quantity=self._quantity,
                price=price,
                quote=quote,
            )
            self._target = 0
            self._quantity = 0.0

        if target.position != 0:
            quantity = self._open_quantity(price)
            last_receipt = self._submit_order(
                target=target.position,
                close=False,
                quantity=quantity,
                price=price,
                quote=quote,
            )
            self._quantity = quantity
        self._target = target.position
        return last_receipt

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
                # Order submission is synchronous broker I/O (ccxt /
                # requests); run it off the event loop so one engine's order
                # cannot block every other engine, the watchdog, or quoting.
                await asyncio.to_thread(self.process_bar, bar)
            except (RiskRejected, InsufficientCash) as exc:
                # A risk gate (missing/stale quote, spread, notional, daily
                # cap, ...) refused this bar's order, or the simulated/ledger
                # cash cannot afford the fill. That is a skip, not a crash:
                # stay alive and re-evaluate on the next bar when conditions
                # (e.g. a fresh quote or after a close) may have recovered.
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
