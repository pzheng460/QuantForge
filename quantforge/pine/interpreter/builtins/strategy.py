"""strategy.* built-in functions — position tracking and order management.

Implements: strategy(), strategy.entry, strategy.exit, strategy.close,
strategy.close_all, strategy.position_size, strategy.position_avg_price.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Direction(IntEnum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Order:
    """A pending or filled order."""

    id: str
    direction: Direction  # LONG or SHORT
    qty: float
    limit_price: float | None = None
    stop_price: float | None = None
    bar_index: int = 0  # bar on which the order was placed
    is_exit: bool = False
    from_id: str = ""  # for exit orders, the entry id they target
    # TP/SL for strategy.exit
    profit: float | None = None  # take-profit distance
    loss: float | None = None  # stop-loss distance
    comment: str = ""


@dataclass
class Trade:
    """A completed round-trip trade."""

    entry_bar: int
    exit_bar: int
    direction: Direction
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    commission: float = 0.0
    entry_id: str = ""
    exit_id: str = ""
    entry_time: int = 0  # timestamp
    exit_time: int = 0


@dataclass
class Position:
    """Current open position."""

    direction: Direction = Direction.FLAT
    qty: float = 0.0
    avg_price: float = 0.0
    entry_bar: int = 0
    entry_id: str = ""
    entry_time: int = 0


class StrategyEngine:
    """Manages strategy state: positions, orders, trades, equity.

    This is the core strategy execution engine used by the Pine interpreter.
    Orders placed on bar N are filled on bar N+1's open (default behavior).
    """

    def __init__(
        self,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        pyramiding: int = 1,
        default_qty_type: str = "fixed",  # "fixed", "percent_of_equity", "cash"
        default_qty_value: float = 1.0,
        process_orders_on_close: bool = False,
        currency: str = "USD",
    ):
        self.initial_capital = initial_capital
        self.commission = commission  # per-order commission rate (e.g. 0.001 = 0.1%)
        self.slippage = slippage  # per-order slippage in price units
        self.pyramiding = pyramiding
        self.default_qty_type = default_qty_type
        self.default_qty_value = default_qty_value
        self.process_orders_on_close = process_orders_on_close
        self.currency = currency

        # State
        self.position = Position()
        self.pending_orders: list[Order] = []
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = []

        # Tracking
        self._entry_count: dict[str, int] = {}  # entry id -> count (for pyramiding)
        self._exit_orders: dict[str, Order] = {}  # entry id -> exit order (TP/SL)
        self._bar_index = 0
        self._bar_time = 0

    @property
    def position_size(self) -> float:
        """Current position size (positive=long, negative=short, 0=flat)."""
        if self.position.direction == Direction.FLAT:
            return 0.0
        sign = 1.0 if self.position.direction == Direction.LONG else -1.0
        return sign * self.position.qty

    @property
    def position_avg_price(self) -> float:
        return self.position.avg_price

    @property
    def equity(self) -> float:
        return self.initial_capital + sum(t.pnl - t.commission for t in self.trades)

    @property
    def open_profit(self) -> float:
        """Unrealised PnL of open position."""
        return 0.0  # updated during bar processing

    def set_bar(self, bar_index: int, bar_time: int = 0) -> None:
        self._bar_index = bar_index
        self._bar_time = bar_time

    # ------------------------------------------------------------------
    # Order API (called by Pine script evaluation)
    # ------------------------------------------------------------------

    def entry(
        self,
        id: str,
        direction: Direction,
        qty: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        comment: str = "",
    ) -> None:
        """strategy.entry() — queue an entry order."""
        if qty is None:
            qty = self._resolve_qty()

        # Check pyramiding
        if self.position.direction == direction:
            open_entries = self._entry_count.get(id, 0)
            if open_entries >= self.pyramiding:
                return  # pyramiding limit reached

        order = Order(
            id=id,
            direction=direction,
            qty=qty,
            limit_price=limit,
            stop_price=stop,
            bar_index=self._bar_index,
            comment=comment,
        )
        # Remove any existing pending entry with same id
        self.pending_orders = [
            o for o in self.pending_orders if o.id != id or o.is_exit
        ]
        self.pending_orders.append(order)

    def exit(
        self,
        id: str,
        from_entry: str = "",
        qty: float | None = None,
        profit: float | None = None,
        loss: float | None = None,
        limit: float | None = None,
        stop: float | None = None,
        comment: str = "",
    ) -> None:
        """strategy.exit() — queue an exit order (TP/SL/limit/stop)."""
        order = Order(
            id=id,
            direction=Direction.FLAT,
            qty=qty or 0.0,
            limit_price=limit,
            stop_price=stop,
            bar_index=self._bar_index,
            is_exit=True,
            from_id=from_entry,
            profit=profit,
            loss=loss,
            comment=comment,
        )
        # Replace existing exit for same from_entry
        self.pending_orders = [
            o
            for o in self.pending_orders
            if not (o.is_exit and o.from_id == from_entry)
        ]
        self.pending_orders.append(order)

    def close(self, id: str = "", comment: str = "") -> None:
        """strategy.close() — close position opened by entry `id`."""
        if self.position.direction == Direction.FLAT:
            return
        rev_dir = (
            Direction.SHORT
            if self.position.direction == Direction.LONG
            else Direction.LONG
        )
        order = Order(
            id=f"_close_{id}",
            direction=rev_dir,
            qty=self.position.qty,
            bar_index=self._bar_index,
            is_exit=True,
            from_id=id,
            comment=comment,
        )
        self.pending_orders.append(order)

    def close_all(self, comment: str = "") -> None:
        """strategy.close_all() — close entire position."""
        self.close(id="", comment=comment)

    # ------------------------------------------------------------------
    # Order processing (called by runtime each bar)
    # ------------------------------------------------------------------

    def process_orders(
        self, open_price: float, high: float, low: float, close: float
    ) -> list[Trade]:
        """Process pending orders against current bar's prices.

        Returns list of trades executed this bar.
        """
        new_trades: list[Trade] = []
        remaining: list[Order] = []

        for order in self.pending_orders:
            fill_price = self._get_fill_price(order, open_price, high, low)
            if fill_price is None:
                # Check TP/SL exit orders
                if order.is_exit and (
                    order.profit is not None or order.loss is not None
                ):
                    fill_price = self._check_tp_sl(order, high, low)
                if fill_price is None:
                    remaining.append(order)
                    continue

            # Apply slippage
            if order.direction == Direction.LONG:
                fill_price += self.slippage
            elif order.direction == Direction.SHORT:
                fill_price -= self.slippage

            trade = self._execute_order(order, fill_price)
            if trade is not None:
                new_trades.append(trade)

        self.pending_orders = remaining

        # Record equity
        self.equity_curve.append(self.equity)

        return new_trades

    def _get_fill_price(
        self, order: Order, open_price: float, high: float, low: float
    ) -> float | None:
        """Determine fill price for an order."""
        if (
            order.is_exit
            and not order.limit_price
            and not order.stop_price
            and order.profit is None
            and order.loss is None
        ):
            # Market exit → fill at open
            return open_price

        if order.limit_price is not None:
            if order.direction == Direction.LONG and low <= order.limit_price:
                return min(order.limit_price, open_price)
            if order.direction == Direction.SHORT and high >= order.limit_price:
                return max(order.limit_price, open_price)
            return None

        if order.stop_price is not None:
            if order.direction == Direction.LONG and high >= order.stop_price:
                return max(order.stop_price, open_price)
            if order.direction == Direction.SHORT and low <= order.stop_price:
                return min(order.stop_price, open_price)
            return None

        # Market order → fill at open
        return open_price

    def _check_tp_sl(self, order: Order, high: float, low: float) -> float | None:
        """Check take-profit / stop-loss levels for exit orders."""
        if self.position.direction == Direction.FLAT:
            return None

        entry_price = self.position.avg_price

        if order.profit is not None:
            if self.position.direction == Direction.LONG:
                tp_price = entry_price + order.profit
                if high >= tp_price:
                    return tp_price
            else:
                tp_price = entry_price - order.profit
                if low <= tp_price:
                    return tp_price

        if order.loss is not None:
            if self.position.direction == Direction.LONG:
                sl_price = entry_price - order.loss
                if low <= sl_price:
                    return sl_price
            else:
                sl_price = entry_price + order.loss
                if high >= sl_price:
                    return sl_price

        return None

    def _execute_order(self, order: Order, fill_price: float) -> Trade | None:
        """Execute a filled order, updating position and creating trades."""
        commission = (
            abs(order.qty * fill_price * self.commission)
            if order.qty > 0
            else abs(self.position.qty * fill_price * self.commission)
        )

        if order.is_exit:
            return self._close_position(order, fill_price, commission)

        # Entry order
        if self.position.direction == Direction.FLAT:
            # Open new position
            self.position = Position(
                direction=order.direction,
                qty=order.qty,
                avg_price=fill_price,
                entry_bar=self._bar_index,
                entry_id=order.id,
                entry_time=self._bar_time,
            )
            self._entry_count[order.id] = self._entry_count.get(order.id, 0) + 1
            return None

        if self.position.direction == order.direction:
            # Add to position (pyramiding)
            total_qty = self.position.qty + order.qty
            self.position.avg_price = (
                self.position.avg_price * self.position.qty + fill_price * order.qty
            ) / total_qty
            self.position.qty = total_qty
            self._entry_count[order.id] = self._entry_count.get(order.id, 0) + 1
            return None

        # Reverse position: close current, open new
        trade = self._close_position(order, fill_price, commission)
        if (
            order.qty > self.position.qty
            if self.position.direction != Direction.FLAT
            else True
        ):
            remaining_qty = order.qty - (trade.qty if trade else 0.0)
            if remaining_qty > 0:
                self.position = Position(
                    direction=order.direction,
                    qty=remaining_qty,
                    avg_price=fill_price,
                    entry_bar=self._bar_index,
                    entry_id=order.id,
                    entry_time=self._bar_time,
                )
                self._entry_count[order.id] = 1
        return trade

    def _close_position(
        self, order: Order, fill_price: float, commission: float
    ) -> Trade | None:
        """Close (fully or partially) the current position."""
        if self.position.direction == Direction.FLAT:
            return None

        close_qty = order.qty if order.qty > 0 else self.position.qty
        close_qty = min(close_qty, self.position.qty)

        if self.position.direction == Direction.LONG:
            pnl = (fill_price - self.position.avg_price) * close_qty
        else:
            pnl = (self.position.avg_price - fill_price) * close_qty

        trade = Trade(
            entry_bar=self.position.entry_bar,
            exit_bar=self._bar_index,
            direction=self.position.direction,
            entry_price=self.position.avg_price,
            exit_price=fill_price,
            qty=close_qty,
            pnl=pnl,
            commission=commission,
            entry_id=self.position.entry_id,
            exit_id=order.id,
            entry_time=self.position.entry_time,
            exit_time=self._bar_time,
        )
        self.trades.append(trade)

        # Update position
        remaining = self.position.qty - close_qty
        if remaining <= 1e-10:
            self.position = Position()
            self._entry_count.clear()
        else:
            self.position.qty = remaining

        return trade

    def _resolve_qty(self) -> float:
        """Resolve default quantity based on qty type."""
        if self.default_qty_type == "percent_of_equity":
            return self.equity * (self.default_qty_value / 100.0)
        if self.default_qty_type == "cash":
            return self.default_qty_value
        return self.default_qty_value  # fixed


# Registry of strategy properties accessible from Pine
STRATEGY_PROPERTIES = {
    "long": Direction.LONG,
    "short": Direction.SHORT,
}
