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
    """A pending order to be executed on the next bar."""

    id: str
    direction: Direction
    action: str  # "entry", "exit", "close", "close_all"
    qty: float | None = None
    limit: float | None = None
    stop: float | None = None
    comment: str = ""
    bar_index: int = 0  # bar on which the order was placed


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


@dataclass
class Position:
    """Current open position."""

    direction: Direction | None = None
    qty: float = 0.0
    entry_price: float = 0.0
    entry_bar: int = 0
    comment: str = ""

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
        self.pending_orders: list[Order] = []
        self.trades: list[Trade] = []
        self._equity_curve: list[float] = []
        self._entry_count: int = 0  # for pyramiding

        # Signal callbacks for live trading — called when orders are *placed*
        # (not executed).  The live engine hooks into these to capture signals.
        self._on_entry_cb: _SignalCallback | None = None
        self._on_close_cb: _SignalCallback | None = None
        self._on_exit_cb: _SignalCallback | None = None

    def set_signal_callbacks(
        self,
        on_entry: _SignalCallback | None = None,
        on_close: _SignalCallback | None = None,
        on_exit: _SignalCallback | None = None,
    ) -> None:
        """Register callbacks fired when Pine script places orders.

        Used by the live engine to intercept ``strategy.entry/close/exit``
        calls and route them to real exchange orders.
        """
        self._on_entry_cb = on_entry
        self._on_close_cb = on_close
        self._on_exit_cb = on_exit

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
            qty=qty or self.default_qty,
            limit=limit,
            stop=stop,
            comment=comment,
            bar_index=bar_index,
        )
        self.pending_orders.append(order)
        if self._on_entry_cb is not None:
            self._on_entry_cb(order)

    def place_exit(
        self,
        id: str,
        from_entry: str = "",
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        comment: str = "",
        bar_index: int = 0,
    ) -> None:
        """Queue an exit order."""
        direction = (
            Direction.SHORT
            if (self.position.direction == Direction.LONG)
            else Direction.LONG
        )
        order = Order(
            id=id,
            direction=direction,
            action="exit",
            qty=qty,
            limit=limit,
            stop=stop,
            comment=comment,
            bar_index=bar_index,
        )
        self.pending_orders.append(order)
        if self._on_exit_cb is not None:
            self._on_exit_cb(order)

    def place_close(self, id: str = "", comment: str = "", bar_index: int = 0) -> None:
        """Queue a close order for current position."""
        if self.position.is_flat:
            return
        order = Order(
            id=id or "close",
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

    def execute_pending(self, bar_open: float, bar_index: int) -> None:
        """Execute all pending orders at the given bar's open price.

        Called at the START of each new bar (orders from previous bar execute here).
        """
        orders = self.pending_orders
        self.pending_orders = []

        for order in orders:
            fill_price = bar_open

            # Check limit/stop
            if order.limit is not None:
                if order.direction == Direction.LONG and fill_price > order.limit:
                    continue
                if order.direction == Direction.SHORT and fill_price < order.limit:
                    continue
                fill_price = order.limit
            if order.stop is not None:
                if order.direction == Direction.LONG and fill_price < order.stop:
                    continue
                if order.direction == Direction.SHORT and fill_price > order.stop:
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
        raw_qty = order_qty or self.default_qty

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
        # If already in opposite direction, close first
        if not self.position.is_flat and self.position.direction != order.direction:
            self._close_position(price, bar_index, comment=order.comment)

        if not self.position.is_flat and self.position.direction == order.direction:
            # Pyramiding check
            if self._entry_count >= self.pyramiding:
                return

        qty = self._resolve_qty(order.qty, price)
        comm = qty * price * self.commission

        if self.position.is_flat:
            self.position = Position(
                direction=order.direction,
                qty=qty,
                entry_price=price,
                entry_bar=bar_index,
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

        self._entry_count += 1
        self.equity -= comm

    def _execute_close(self, order: Order, price: float, bar_index: int) -> None:
        """Execute a close/exit order."""
        if self.position.is_flat:
            return
        qty = order.qty if order.qty else self.position.qty
        qty = min(qty, self.position.qty)
        self._close_position(price, bar_index, qty=qty, comment=order.comment)

    def _close_position(
        self, price: float, bar_index: int, qty: float | None = None, comment: str = ""
    ) -> None:
        """Close (part of) the current position."""
        if self.position.is_flat:
            return

        close_qty = qty if qty is not None else self.position.qty
        close_qty = min(close_qty, self.position.qty)

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
            )
        )

        self.equity += pnl
        self.position.qty -= close_qty

        if self.position.qty <= 0:
            self.position = Position()
            self._entry_count = 0

    def update_equity(self, current_price: float) -> None:
        """Track unrealised equity at end of bar."""
        unrealised = 0.0
        if not self.position.is_flat:
            if self.position.direction == Direction.LONG:
                unrealised = (
                    current_price - self.position.entry_price
                ) * self.position.qty
            else:
                unrealised = (
                    self.position.entry_price - current_price
                ) * self.position.qty
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
