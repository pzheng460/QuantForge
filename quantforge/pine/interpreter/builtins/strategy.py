"""Strategy builtins: strategy.entry, strategy.exit, strategy.close, strategy.close_all.

Orders execute on NEXT bar open by default (matching TradingView behaviour).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

# Callback type for live signal interception
_SignalCallback = Callable[..., None]


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Order:
    """A pending order to be executed on the next bar.

    Conditional fields:
      - ``limit``: take-profit price (LONG ⇒ fills when price ≤ limit;
        SHORT ⇒ fills when price ≥ limit).
      - ``stop``:  stop price (LONG ⇒ fills when price ≥ stop;
        SHORT ⇒ fills when price ≤ stop). For exit-side direction (the
        OPPOSITE of position direction), the trigger direction flips
        accordingly — see ``execute_pending`` for the canonical table.
      - ``trail_points``: trailing-stop distance in absolute price units.
        The internal stop is ratcheted by favorable moves of the
        underlying position and the trail is recalculated each bar.
    """

    id: str
    direction: Direction
    action: str  # "entry", "exit", "close", "close_all"
    from_entry: str = ""
    entry_ids: tuple[str, ...] = ()
    qty: float | None = None
    limit: float | None = None
    stop: float | None = None
    trail_points: float | None = None
    comment: str = ""
    bar_index: int = 0  # bar on which the order was placed
    # Mutable per-bar state for trailing-stop orders. The interpreter
    # updates this in ``execute_pending`` as the position price moves.
    # Not part of the user-facing API.
    _trail_high: float = 0.0  # best favorable price seen since order queued
    _trail_low: float = 0.0   # worst favorable price seen since order queued (short)
    _trail_stop: float | None = None  # current trailing stop price


@dataclass
class Trade:
    """A completed trade."""

    entry_bar: int
    entry_price: float
    exit_bar: int
    exit_price: float
    direction: Direction
    qty: float
    pnl: float
    comment_entry: str = ""
    comment_exit: str = ""
    mfe: float = 0.0  # Maximum Favorable Excursion (absolute, per unit)
    mae: float = 0.0  # Maximum Adverse Excursion (absolute, per unit, negative)


@dataclass
class Position:
    """Current open position."""

    direction: Direction | None = None
    qty: float = 0.0
    entry_price: float = 0.0
    entry_bar: int = 0
    entry_id: str = ""
    comment: str = ""
    _mfe: float = 0.0  # best favorable move per unit
    _mae: float = 0.0  # worst adverse move per unit (negative)

    @property
    def is_flat(self) -> bool:
        return self.direction is None or self.qty == 0.0

    @property
    def size(self) -> float:
        if self.direction == Direction.LONG:
            return self.qty
        elif self.direction == Direction.SHORT:
            return -self.qty
        return 0.0


class StrategyContext:
    """Tracks positions, pending orders, and trade history."""

    # Pine Script qty type constants
    QTY_FIXED = "fixed"
    QTY_CASH = "cash"
    QTY_PERCENT = "percent_of_equity"

    def __init__(
        self,
        initial_capital: float = 100000.0,
        default_qty: float = 1.0,
        default_qty_type: str = "fixed",
        commission: float = 0.0,
        pyramiding: int = 1,
    ) -> None:
        self.initial_capital = initial_capital
        self.default_qty = default_qty
        self.default_qty_type = default_qty_type
        self.commission = commission
        self.pyramiding = pyramiding
        self.equity = initial_capital

        self.position = Position()
        self._entry_lots: list[Position] = []
        self.pending_orders: list[Order] = []
        self.trades: list[Trade] = []
        self._equity_curve: list[float] = []
        self._entry_count: int = 0  # for pyramiding

        # ── Signal callbacks ────────────────────────────────────────────
        # Queue-time callbacks fire when the Pine script *places* an order
        # (`strategy.entry/close/exit`). These are used purely for signal
        # logging (the live engine records each as a SignalRecord). They
        # MUST NOT submit anything to the exchange — for entries with no
        # stop/limit the actual fill happens at the next bar's open, and
        # for conditional exits the fill happens when bar.high/low crosses
        # the trigger price, possibly many bars later.
        self._on_entry_cb: _SignalCallback | None = None
        self._on_close_cb: _SignalCallback | None = None
        self._on_exit_cb: _SignalCallback | None = None
        # Fill-time callbacks fire when ``_execute_entry`` /
        # ``_close_position`` actually fill an order. The live engine uses
        # these to submit a market order to the exchange — at this point
        # the fill price is finalized, so exchange order timing matches
        # backtest semantics.
        # Signature:
        #   on_entry_fill(direction: str, price: float, qty: float, order_id: str)
        #   on_close_fill(direction: str, price: float, qty: float, order_id: str)
        self._on_entry_fill_cb: _SignalCallback | None = None
        self._on_close_fill_cb: _SignalCallback | None = None

    def set_signal_callbacks(
        self,
        on_entry: _SignalCallback | None = None,
        on_close: _SignalCallback | None = None,
        on_exit: _SignalCallback | None = None,
        on_entry_fill: _SignalCallback | None = None,
        on_close_fill: _SignalCallback | None = None,
    ) -> None:
        """Register callbacks fired when Pine script places or fills orders.

        See class-level docstring on the difference between queue-time
        (``on_entry``/``on_close``/``on_exit``) and fill-time
        (``on_entry_fill``/``on_close_fill``) callbacks.
        """
        self._on_entry_cb = on_entry
        self._on_close_cb = on_close
        self._on_exit_cb = on_exit
        self._on_entry_fill_cb = on_entry_fill
        self._on_close_fill_cb = on_close_fill

    def reset_trading_state(self) -> None:
        """Clear orders, positions, trades, and equity while preserving config.

        Used after indicator warmup: series/calculators remain primed in the
        runtime context, but simulated warmup trades must not leak into the
        formal backtest period.
        """
        self.equity = self.initial_capital
        self.position = Position()
        self._entry_lots = []
        self.pending_orders = []
        self.trades = []
        self._equity_curve = []
        self._entry_count = 0

    def place_entry(
        self,
        id: str,
        direction: Direction,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        comment: str = "",
        bar_index: int = 0,
    ) -> None:
        """Queue an entry order for next-bar execution."""
        order = Order(
            id=id,
            direction=direction,
            action="entry",
            qty=self.default_qty if qty is None else qty,
            limit=limit,
            stop=stop,
            comment=comment,
            bar_index=bar_index,
        )
        self.pending_orders.append(order)
        self._attach_pending_exits_to_entry(order)
        if self._on_entry_cb is not None:
            self._on_entry_cb(order)

    def _attach_pending_exits_to_entry(self, entry: Order) -> None:
        if not self.pending_orders:
            return

        entry_index = len(self.pending_orders) - 1
        moved: list[Order] = []
        kept: list[Order] = []

        for idx, order in enumerate(self.pending_orders):
            if idx == entry_index:
                kept.append(order)
                continue
            should_attach = (
                order.action == "exit"
                and (
                    order.from_entry == entry.id
                    or (not order.from_entry and not order.entry_ids)
                    or entry.id in order.entry_ids
                )
            )
            if not should_attach:
                kept.append(order)
                continue

            order.entry_ids = (entry.id,)
            order.direction = (
                Direction.SHORT
                if entry.direction == Direction.LONG
                else Direction.LONG
            )
            order._trail_high = entry.limit or entry.stop or self.position.entry_price or 0.0
            order._trail_low = entry.limit or entry.stop or self.position.entry_price or 0.0
            order._trail_stop = None
            moved.append(order)

        self.pending_orders = kept + moved

    def place_exit(
        self,
        id: str,
        from_entry: str = "",
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        trail_points: float | None = None,
        comment: str = "",
        bar_index: int = 0,
    ) -> None:
        """Queue a conditional exit order (stop / limit / trailing stop).

        The order PERSISTS across bars until either:
          - its trigger condition fires (bar.high/low crosses the trigger), or
          - the position becomes flat (closed by another order), or
          - the strategy explicitly cancels via id (Pine's
            ``strategy.cancel(id)`` — not yet wired through).
        """
        # TV-compatible idempotency: if an exit with the same id already
        # exists, leave it in place — repeated ``strategy.exit`` calls
        # with identical args are no-ops, and trailing-stop accumulated
        # state must NOT be reset every bar. If the args drift (different
        # stop/limit/trail), update them in place; otherwise return early.
        for idx, existing in enumerate(self.pending_orders):
            if existing.action == "exit" and existing.id == id:
                direction = self._resolve_exit_direction(from_entry)
                entry_ids = self._resolve_exit_entry_ids(from_entry)
                if (
                    existing.stop == stop
                    and existing.limit == limit
                    and existing.trail_points == trail_points
                    and existing.from_entry == from_entry
                    and existing.entry_ids == entry_ids
                    and existing.direction == direction
                ):
                    return  # idempotent
                # Mutate in place — preserve trail accumulator.
                direction_changed = (
                    existing.from_entry != from_entry
                    or existing.entry_ids != entry_ids
                    or existing.direction != direction
                )
                existing.from_entry = from_entry
                existing.entry_ids = entry_ids
                existing.direction = direction
                existing.stop = stop
                existing.limit = limit
                existing.trail_points = trail_points
                existing.qty = qty
                existing.comment = comment
                existing.bar_index = bar_index
                if direction_changed:
                    existing._trail_high = self.position.entry_price or 0.0
                    existing._trail_low = self.position.entry_price or 0.0
                    existing._trail_stop = None
                if direction_changed:
                    self.pending_orders.pop(idx)
                    self.pending_orders.append(existing)
                return

        direction = self._resolve_exit_direction(from_entry)
        entry_ids = self._resolve_exit_entry_ids(from_entry)
        order = Order(
            id=id,
            direction=direction,
            action="exit",
            from_entry=from_entry,
            entry_ids=entry_ids,
            qty=qty,
            limit=limit,
            stop=stop,
            trail_points=trail_points,
            comment=comment,
            bar_index=bar_index,
        )
        # Seed trailing-stop reference price to current position avg price.
        # The first bar that processes this order will refine via bar high/low.
        if trail_points is not None:
            seed = self.position.entry_price or 0.0
            order._trail_high = seed
            order._trail_low = seed
            if direction == Direction.SHORT:
                # LONG SL via trail: stop sits below the running high
                order._trail_stop = seed - trail_points
            else:
                # SHORT SL via trail: stop sits above the running low
                order._trail_stop = seed + trail_points
        self.pending_orders.append(order)
        if self._on_exit_cb is not None:
            self._on_exit_cb(order)

    def _resolve_exit_direction(self, from_entry: str = "") -> Direction:
        """Infer the order side used to close the active or pending entry."""
        if self.position.direction == Direction.LONG:
            return Direction.SHORT
        if self.position.direction == Direction.SHORT:
            return Direction.LONG

        for pending in reversed(self.pending_orders):
            if pending.action != "entry":
                continue
            if from_entry and pending.id != from_entry:
                continue
            return (
                Direction.SHORT
                if pending.direction == Direction.LONG
                else Direction.LONG
            )

        return Direction.LONG

    def _resolve_exit_entry_ids(self, from_entry: str = "") -> tuple[str, ...]:
        if from_entry:
            return (from_entry,)
        if self._entry_lots:
            return tuple(lot.entry_id for lot in self._entry_lots)
        if self.position.entry_id:
            return (self.position.entry_id,)

        pending_ids = [
            pending.id for pending in self.pending_orders if pending.action == "entry"
        ]
        return tuple(pending_ids)

    def place_close(self, id: str = "", comment: str = "", bar_index: int = 0) -> None:
        """Queue a close order for current position."""
        if self.position.is_flat:
            return
        order = Order(
            id=id,
            direction=Direction.SHORT
            if self.position.direction == Direction.LONG
            else Direction.LONG,
            action="close",
            comment=comment,
            bar_index=bar_index,
        )
        self.pending_orders.append(order)
        if self._on_close_cb is not None:
            self._on_close_cb(order)

    def place_close_all(self, comment: str = "", bar_index: int = 0) -> None:
        """Close all positions."""
        self.place_close(id="close_all", comment=comment, bar_index=bar_index)

    def cancel_pending(self, order_id: str | None = None) -> int:
        """Remove pending orders matching ``order_id`` (or all if ``None``).

        Mirrors TradingView's ``strategy.cancel(id)`` / ``strategy.cancel_all()``.
        Returns the number of orders removed.
        """
        before = len(self.pending_orders)
        if order_id is None:
            self.pending_orders = []
        else:
            self.pending_orders = [
                o for o in self.pending_orders if o.id != order_id
            ]
        return before - len(self.pending_orders)

    def execute_pending(self, bar, bar_index: int) -> None:
        """Process pending orders against a fully-resolved bar.

        Called at the START of each new bar with that bar's full OHLC.

        * **Immediate** (no ``limit`` / ``stop`` / ``trail_points``):
          unconditional fill at ``bar.open``.

        * **Conditional**: trigger only when the bar's range crosses the
          relevant threshold. Fill price = trigger clamped to the bar's
          gap direction. If the trigger doesn't fire this bar the order
          is re-queued so it can fire on a later bar — TradingView's
          semantics for ``strategy.exit``.

        Conditional exit orders are also implicitly cancelled if the
        position becomes flat (e.g., another order closed the position
        earlier in the same execute_pending pass).

        Accepts ``bar_open`` (float) for backward compatibility with old
        call sites that haven't been migrated yet.
        """
        if hasattr(bar, "open"):
            bar_open = bar.open
            bar_high = bar.high
            bar_low = bar.low
        else:
            bar_open = float(bar)
            bar_high = bar_open
            bar_low = bar_open

        orders = self.pending_orders
        self.pending_orders = []

        for order in orders:
            # Implicit cancellation of stale exits after a close in this
            # same pass.
            if order.action == "exit" and self.position.is_flat:
                continue

            # ── Trailing stop: ratchet the running stop from this bar ──
            if order.trail_points is not None and order.action == "exit":
                tp = order.trail_points
                if order.direction == Direction.SHORT:
                    # Position is LONG → trail above the running HIGH.
                    order._trail_high = max(order._trail_high, bar_high)
                    new_stop = order._trail_high - tp
                    if order._trail_stop is None or new_stop > order._trail_stop:
                        order._trail_stop = new_stop
                    order.stop = order._trail_stop
                else:
                    # Position is SHORT → trail below the running LOW.
                    if order._trail_low == 0.0:
                        order._trail_low = bar_low
                    else:
                        order._trail_low = min(order._trail_low, bar_low)
                    new_stop = order._trail_low + tp
                    if order._trail_stop is None or new_stop < order._trail_stop:
                        order._trail_stop = new_stop
                    order.stop = order._trail_stop

            # ── Determine trigger + fill price ────────────────────────
            is_conditional = order.limit is not None or order.stop is not None
            triggered = not is_conditional
            fill_price = bar_open

            if is_conditional:
                # Canonical trigger table:
                #   BUY (LONG)   limit  →  triggers when bar.low  ≤ limit
                #   BUY (LONG)   stop   →  triggers when bar.high ≥ stop
                #   SELL (SHORT) limit  →  triggers when bar.high ≥ limit
                #   SELL (SHORT) stop   →  triggers when bar.low  ≤ stop
                # Fill price respects gap direction:
                #   buy limit → min(open, limit)   (gapped below the limit)
                #   buy stop  → max(open, stop)    (gapped above the stop)
                #   sell limit → max(open, limit)
                #   sell stop  → min(open, stop)
                if order.direction == Direction.LONG:
                    if order.limit is not None and bar_low <= order.limit:
                        triggered = True
                        fill_price = min(bar_open, order.limit)
                    if order.stop is not None and bar_high >= order.stop:
                        triggered = True
                        fill_price = max(bar_open, order.stop)
                else:  # SHORT
                    if order.limit is not None and bar_high >= order.limit:
                        triggered = True
                        fill_price = max(bar_open, order.limit)
                    if order.stop is not None and bar_low <= order.stop:
                        triggered = True
                        fill_price = min(bar_open, order.stop)

            if not triggered:
                # Persist — order lives until a future bar triggers it or
                # the position goes flat from another order.
                self.pending_orders.append(order)
                continue

            if order.action == "entry":
                self._execute_entry(order, fill_price, bar_index)
            elif order.action in ("exit", "close"):
                self._execute_close(order, fill_price, bar_index)

    def _resolve_qty(self, order_qty: float | None, price: float) -> float:
        """Resolve order quantity based on default_qty_type.

        Supports:
        - fixed: use qty directly (contracts/units)
        - percent_of_equity: qty is a percentage (0-100) of current equity
        - cash: qty is a dollar amount to allocate
        """
        raw_qty = self.default_qty if order_qty is None else order_qty

        if self.default_qty_type == self.QTY_PERCENT:
            # percent_of_equity: raw_qty is 0-100, convert to position size
            if price <= 0:
                return raw_qty
            dollar_amount = self.equity * (raw_qty / 100.0)
            return dollar_amount / price
        elif self.default_qty_type == self.QTY_CASH:
            # cash: raw_qty is dollar amount
            if price <= 0:
                return raw_qty
            return raw_qty / price
        else:
            # fixed: raw_qty is number of contracts
            return raw_qty

    def _execute_entry(self, order: Order, price: float, bar_index: int) -> None:
        """Execute an entry order."""
        qty = self._resolve_qty(order.qty, price)
        if qty <= 0:
            return

        # If already in opposite direction, close first. ``_close_position``
        # fires ``_on_close_fill_cb`` itself, so we don't need to here.
        # We tag the implicit close with a synthetic id so the live bridge
        # can dedup against the queue-time submission (see OrderBridge).
        if not self.position.is_flat and self.position.direction != order.direction:
            self._close_position(
                price, bar_index, comment=order.comment,
                order_id=f"{order.id}__reverse",
            )

        if not self.position.is_flat and self.position.direction == order.direction:
            # Pyramiding cap — silently refuse the entry. No callback fires.
            if self._entry_count >= self.pyramiding:
                return

        comm = qty * price * self.commission

        if self.position.is_flat:
            self.position = Position(
                direction=order.direction,
                qty=qty,
                entry_price=price,
                entry_bar=bar_index,
                entry_id=order.id,
                comment=order.comment,
            )
        else:
            # Average in (pyramiding)
            total_qty = self.position.qty + qty
            avg_price = (
                self.position.entry_price * self.position.qty + price * qty
            ) / total_qty
            self.position.qty = total_qty
            self.position.entry_price = avg_price

        self._entry_lots.append(
            Position(
                direction=order.direction,
                qty=qty,
                entry_price=price,
                entry_bar=bar_index,
                entry_id=order.id,
                comment=order.comment or order.id,
            )
        )
        self._entry_count += 1
        self.equity -= comm

        # Fire fill-time callback for live engine — submits the real
        # exchange order at the same moment Pine records the fill, so
        # exchange timing matches backtest semantics.
        if self._on_entry_fill_cb is not None:
            try:
                self._on_entry_fill_cb(
                    direction=order.direction.value,
                    price=price,
                    qty=qty,
                    order_id=order.id,
                )
            except Exception:
                # Never let a buggy callback break the strategy run.
                import logging
                logging.getLogger(__name__).exception(
                    "on_entry_fill callback raised"
                )

    def _execute_close(self, order: Order, price: float, bar_index: int) -> None:
        """Execute a close/exit order."""
        if self.position.is_flat:
            return
        target_entry_id = ""
        target_entry_ids: tuple[str, ...] = ()
        if order.action == "close" and order.id not in ("", "close_all"):
            target_entry_id = order.id
            if not self._has_entry_lot(target_entry_id):
                return
        if order.action == "exit":
            target_entry_id = order.from_entry
            target_entry_ids = order.entry_ids
            if target_entry_id and not self._has_entry_lot(target_entry_id):
                return
            if target_entry_ids and not self._has_any_entry_lot(target_entry_ids):
                return
        if order.qty is not None:
            qty = order.qty
        elif target_entry_ids and self._entry_lots:
            qty = sum(
                lot.qty for lot in self._entry_lots if lot.entry_id in target_entry_ids
            )
        elif target_entry_id and self._entry_lots:
            qty = sum(
                lot.qty for lot in self._entry_lots if lot.entry_id == target_entry_id
            )
        else:
            qty = self.position.qty
        qty = min(qty, self.position.qty)
        if qty <= 0:
            return
        self._close_position(
            price,
            bar_index,
            qty=qty,
            comment=order.comment,
            order_id=order.id,
            target_entry_id=target_entry_id,
            target_entry_ids=target_entry_ids,
        )

    def _has_entry_lot(self, entry_id: str) -> bool:
        if not entry_id:
            return True
        if self._entry_lots:
            return any(lot.entry_id == entry_id for lot in self._entry_lots)
        return entry_id == self.position.entry_id

    def _has_any_entry_lot(self, entry_ids: tuple[str, ...]) -> bool:
        if not entry_ids:
            return True
        if self._entry_lots:
            return any(lot.entry_id in entry_ids for lot in self._entry_lots)
        return self.position.entry_id in entry_ids

    def _close_position(
        self,
        price: float,
        bar_index: int,
        qty: float | None = None,
        comment: str = "",
        order_id: str = "",
        target_entry_id: str = "",
        target_entry_ids: tuple[str, ...] = (),
    ) -> None:
        """Close (part of) the current position."""
        if self.position.is_flat:
            return

        close_qty = qty if qty is not None else self.position.qty
        close_qty = min(close_qty, self.position.qty)
        if close_qty <= 0:
            return
        closed_direction = self.position.direction  # capture before reset

        if self._entry_lots:
            actual_closed_qty = self._close_entry_lots(
                price=price,
                bar_index=bar_index,
                qty=close_qty,
                comment=comment,
                target_entry_id=target_entry_id,
                target_entry_ids=target_entry_ids,
            )

            if self.position.qty <= 0:
                self.position = Position()
                self._entry_count = 0
            else:
                self._rebuild_position_from_lots()
                self._entry_count = len(self._entry_lots)

            if self._on_close_fill_cb is not None and closed_direction is not None:
                try:
                    self._on_close_fill_cb(
                        direction=closed_direction.value,
                        price=price,
                        qty=actual_closed_qty,
                        order_id=order_id,
                    )
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(
                        "on_close_fill callback raised"
                    )
            return

        if self.position.direction == Direction.LONG:
            pnl = (price - self.position.entry_price) * close_qty
        else:
            pnl = (self.position.entry_price - price) * close_qty

        comm = close_qty * price * self.commission
        pnl -= comm

        self.trades.append(
            Trade(
                entry_bar=self.position.entry_bar,
                entry_price=self.position.entry_price,
                exit_bar=bar_index,
                exit_price=price,
                direction=self.position.direction,
                qty=close_qty,
                pnl=pnl,
                comment_entry=self.position.comment,
                comment_exit=comment,
                mfe=self.position._mfe * close_qty,
                mae=self.position._mae * close_qty,
            )
        )

        self.equity += pnl
        self.position.qty -= close_qty

        closed_direction = self.position.direction  # capture before reset

        if self.position.qty <= 0:
            self.position = Position()
            self._entry_count = 0

        # Fire fill-time callback for live engine — see _execute_entry.
        if self._on_close_fill_cb is not None and closed_direction is not None:
            try:
                self._on_close_fill_cb(
                    direction=closed_direction.value,
                    price=price,
                    qty=close_qty,
                    order_id=order_id,
                )
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "on_close_fill callback raised"
                )

    def _close_entry_lots(
        self,
        price: float,
        bar_index: int,
        qty: float,
        comment: str = "",
        target_entry_id: str = "",
        target_entry_ids: tuple[str, ...] = (),
    ) -> float:
        remaining = qty
        closed_qty_total = 0.0
        kept_lots: list[Position] = []

        for lot in self._entry_lots:
            if remaining <= 0:
                kept_lots.append(lot)
                continue
            if target_entry_id and lot.entry_id != target_entry_id:
                kept_lots.append(lot)
                continue
            if target_entry_ids and lot.entry_id not in target_entry_ids:
                kept_lots.append(lot)
                continue

            close_qty = min(lot.qty, remaining)
            if lot.direction == Direction.LONG:
                pnl = (price - lot.entry_price) * close_qty
            else:
                pnl = (lot.entry_price - price) * close_qty

            comm = close_qty * price * self.commission
            pnl -= comm

            self.trades.append(
                Trade(
                    entry_bar=lot.entry_bar,
                    entry_price=lot.entry_price,
                    exit_bar=bar_index,
                    exit_price=price,
                    direction=lot.direction,
                    qty=close_qty,
                    pnl=pnl,
                    comment_entry=lot.comment,
                    comment_exit=comment,
                    mfe=self.position._mfe * close_qty,
                    mae=self.position._mae * close_qty,
                )
            )
            self.equity += pnl
            self.position.qty -= close_qty
            lot.qty -= close_qty
            remaining -= close_qty
            closed_qty_total += close_qty

            if lot.qty > 0:
                kept_lots.append(lot)

        self._entry_lots = kept_lots
        return closed_qty_total

    def _rebuild_position_from_lots(self) -> None:
        if not self._entry_lots:
            self.position = Position()
            return

        total_qty = sum(lot.qty for lot in self._entry_lots)
        if total_qty <= 0:
            self.position = Position()
            return

        first = self._entry_lots[0]
        avg_price = (
            sum(lot.entry_price * lot.qty for lot in self._entry_lots) / total_qty
        )
        self.position.direction = first.direction
        self.position.qty = total_qty
        self.position.entry_price = avg_price
        self.position.entry_bar = first.entry_bar
        self.position.entry_id = first.entry_id
        self.position.comment = first.comment

    def update_equity(
        self,
        current_price: float,
        bar_high: float | None = None,
        bar_low: float | None = None,
    ) -> None:
        """Track unrealised equity at end of bar and update MFE/MAE."""
        unrealised = 0.0
        if not self.position.is_flat:
            if self.position.direction == Direction.LONG:
                unrealised = (
                    current_price - self.position.entry_price
                ) * self.position.qty
                # MFE/MAE tracking using bar high/low
                if bar_high is not None:
                    fav = bar_high - self.position.entry_price
                    self.position._mfe = max(self.position._mfe, fav)
                if bar_low is not None:
                    adv = bar_low - self.position.entry_price
                    self.position._mae = min(self.position._mae, adv)
            else:
                unrealised = (
                    self.position.entry_price - current_price
                ) * self.position.qty
                # MFE/MAE for short: favorable = price drops, adverse = price rises
                if bar_low is not None:
                    fav = self.position.entry_price - bar_low
                    self.position._mfe = max(self.position._mfe, fav)
                if bar_high is not None:
                    adv = self.position.entry_price - bar_high
                    self.position._mae = min(self.position._mae, adv)
        self._equity_curve.append(self.equity + unrealised)

    @property
    def equity_curve(self) -> list[float]:
        return list(self._equity_curve)

    @property
    def net_profit(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def position_size(self) -> float:
        return self.position.size
