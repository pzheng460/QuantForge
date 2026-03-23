"""Bridges Pine strategy signals to real exchange orders.

When the Pine interpreter calls ``strategy.entry`` / ``strategy.close`` /
``strategy.exit``, the :class:`OrderBridge` translates those into exchange
order submissions via the QuantForge connector layer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from quantforge.pine.interpreter.builtins.strategy import Order

logger = logging.getLogger(__name__)


@dataclass
class SignalRecord:
    """Lightweight record of a signal captured from the Pine interpreter."""

    order_id: str
    action: str  # "entry", "close", "exit"
    direction: str  # "long" or "short"
    qty: float
    limit: float | None = None
    stop: float | None = None
    comment: str = ""
    bar_index: int = 0
    timestamp: float = 0.0
    price: float = 0.0


@dataclass
class VirtualTrade:
    """Tracks a completed virtual trade for demo P&L."""

    direction: str
    entry_price: float
    exit_price: float
    qty: float  # actual position quantity in base currency
    pnl: float  # P&L in USDT (price_diff * qty)
    entry_time: float
    exit_time: float


@dataclass
class DemoTracker:
    """Tracks virtual P&L in demo mode."""

    initial_capital: float = 100_000.0
    symbol: str = ""
    position_size_usdt: float = 100.0
    leverage: int = 1
    trades: list[VirtualTrade] = field(default_factory=list)
    _entry_price: float = 0.0
    _entry_time: float = 0.0
    _position_side: str | None = None
    _position_qty: float = 0.0  # base currency qty for current position

    def on_entry(self, direction: str, price: float) -> None:
        """Record a new entry."""
        # If reversing, close the old position first
        if self._position_side and self._position_side != direction:
            self.on_close(price)
        self._entry_price = price
        self._entry_time = time.time()
        self._position_side = direction
        # Calculate actual position qty: (notional * leverage) / price
        if price > 0:
            self._position_qty = (self.position_size_usdt * self.leverage) / price
        else:
            self._position_qty = 0.0

    def on_close(self, price: float) -> None:
        """Close current position and record P&L."""
        if not self._position_side or self._entry_price == 0:
            return
        if self._position_side == "long":
            pnl = (price - self._entry_price) * self._position_qty
        else:
            pnl = (self._entry_price - price) * self._position_qty
        self.trades.append(VirtualTrade(
            direction=self._position_side,
            entry_price=self._entry_price,
            exit_price=price,
            qty=self._position_qty,
            pnl=pnl,
            entry_time=self._entry_time,
            exit_time=time.time(),
        ))
        self._position_side = None
        self._entry_price = 0.0
        self._position_qty = 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.pnl > 0)
        return wins / len(self.trades)

    @property
    def unrealized_pnl(self) -> float:
        """Current unrealized P&L (needs current price)."""
        return 0.0  # Updated externally

    def summary(self, current_price: float = 0.0) -> str:
        """Generate human-readable summary."""
        realized = self.total_pnl
        unrealized = 0.0
        if self._position_side and self._entry_price > 0 and current_price > 0:
            if self._position_side == "long":
                unrealized = (current_price - self._entry_price) * self._position_qty
            else:
                unrealized = (self._entry_price - current_price) * self._position_qty

        total = realized + unrealized
        lines = [
            f"📊 Demo P&L Summary",
            f"  Realized:   ${realized:+,.2f} ({self.total_trades} trades, "
            f"WR {self.win_rate:.0%})",
            f"  Unrealized: ${unrealized:+,.2f} "
            f"({'FLAT' if not self._position_side else self._position_side.upper()})",
            f"  Total:      ${total:+,.2f}",
        ]
        if self.trades:
            last = self.trades[-1]
            lines.append(
                f"  Last trade: {last.direction.upper()} "
                f"${last.entry_price:,.0f}→${last.exit_price:,.0f} "
                f"= ${last.pnl:+,.2f}"
            )
        return "\n".join(lines)

    def to_dict(self, current_price: float = 0.0) -> dict:
        """Serialize to dict matching LivePerformanceOut schema."""
        from datetime import datetime, timezone

        realized = self.total_pnl
        unrealized = 0.0
        if self._position_side and self._entry_price > 0 and current_price > 0:
            if self._position_side == "long":
                unrealized = (current_price - self._entry_price) * self._position_qty
            else:
                unrealized = (self._entry_price - current_price) * self._position_qty

        current_balance = self.initial_capital + realized + unrealized
        peak = self.initial_capital
        max_dd = 0.0
        running = self.initial_capital
        for t in self.trades:
            running += t.pnl
            if running > peak:
                peak = running
            dd = (peak - running) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Current drawdown
        current_dd = 0.0
        if peak > 0:
            current_dd = (peak - current_balance) / peak * 100

        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]

        avg_win_pct = 0.0
        if winning:
            avg_win_pct = sum(
                t.pnl / t.entry_price * 100 for t in winning
            ) / len(winning)
        avg_loss_pct = 0.0
        if losing:
            avg_loss_pct = sum(
                t.pnl / t.entry_price * 100 for t in losing
            ) / len(losing)

        gross_wins = sum(t.pnl for t in winning)
        gross_losses = abs(sum(t.pnl for t in losing))
        pf = gross_wins / gross_losses if gross_losses > 0 else 0.0

        now_str = datetime.now(timezone.utc).isoformat()
        start_str = ""
        if self.trades:
            start_str = datetime.fromtimestamp(
                self.trades[0].entry_time, tz=timezone.utc
            ).isoformat()

        trades_out = []
        for t in self.trades:
            # pnl_pct = pnl / notional_value * 100
            notional = t.entry_price * t.qty if t.qty > 0 else 1.0
            pnl_pct = t.pnl / notional * 100 if notional > 0 else 0.0
            trades_out.append({
                "symbol": self.symbol,
                "side": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "amount": t.qty,
                "entry_time": datetime.fromtimestamp(
                    t.entry_time, tz=timezone.utc
                ).isoformat(),
                "exit_time": datetime.fromtimestamp(
                    t.exit_time, tz=timezone.utc
                ).isoformat(),
                "pnl": t.pnl,
                "pnl_pct": pnl_pct,
                "exit_reason": "",
            })

        return {
            "start_time": start_str,
            "last_update": now_str,
            "mesa_index": 0,
            "config_name": "",
            "initial_balance": self.initial_capital,
            "current_balance": current_balance,
            "peak_balance": peak,
            "total_return_pct": (
                (current_balance - self.initial_capital)
                / self.initial_capital
                * 100
            ),
            "total_pnl": realized + unrealized,
            "max_drawdown_pct": max_dd,
            "current_drawdown_pct": current_dd,
            "total_trades": len(self.trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": self.win_rate * 100,
            "avg_win_pct": avg_win_pct,
            "avg_loss_pct": avg_loss_pct,
            "profit_factor": pf,
            "trades": trades_out,
        }


class OrderBridge:
    """Converts Pine strategy.entry/close/exit calls into exchange orders.

    In *demo* mode orders are only logged.  In *live* mode they are submitted
    to the exchange via ``_submit_order``.

    Parameters
    ----------
    demo : bool
        If ``True`` (default) only log signals, don't submit real orders.
    position_size_usdt : float
        Notional position size in USDT.  Used to compute quantity when the
        Pine script uses ``default_qty_value`` of 1 contract.
    """

    def __init__(
        self,
        demo: bool = True,
        position_size_usdt: float = 100.0,
        leverage: int = 1,
        connector=None,
        symbol: str = "",
    ) -> None:
        self.demo = demo
        self.position_size_usdt = position_size_usdt
        self.leverage = leverage

        # Accumulated signal history
        self.signals: list[SignalRecord] = []

        # CcxtConnector instance for real order submission
        self._connector = connector

        # External order submitter — set by the live engine / connector layer.
        # Signature: async submit(symbol, side, qty, order_type, price=None)
        self._submit_fn = None

        # Current position tracking (mirrors Pine StrategyContext)
        self._position_side: str | None = None  # "long" / "short" / None
        self._position_qty: float = 0.0

        # P&L tracking (always enabled for web dashboard visibility)
        self._demo_tracker = DemoTracker(
            symbol=symbol,
            position_size_usdt=position_size_usdt,
            leverage=leverage,
        )
        self._last_price: float = 0.0

    # --- Callbacks wired into StrategyContext ---

    def _submit_order(
        self,
        side: str,
        qty: float,
        action: str,
        limit: float | None = None,
        stop: float | None = None,
    ) -> None:
        """Submit an order via the connector or legacy _submit_fn.

        The Pine interpreter computes *qty* in its own units (e.g. percent
        of equity).  For real exchange submission we convert to an actual
        base-currency quantity using ``position_size_usdt / current_price``.
        """
        if self.demo:
            return

        # Legacy callback path
        if self._submit_fn:
            self._submit_fn(side=side, qty=qty, action=action, limit=limit, stop=stop)
            return

        # CcxtConnector path
        if self._connector is None:
            logger.warning("No connector configured — order not submitted")
            return

        # Convert Pine qty → exchange qty based on position size and price
        exchange_qty = qty
        if self._last_price > 0 and self.position_size_usdt > 0:
            exchange_qty = round(self.position_size_usdt / self._last_price, 6)

        try:
            reduce_only = action in ("close", "exit")
            logger.info(
                "Exchange order: %s %s qty=%.6f (pine_qty=%.2f, usdt=%.0f, price=%.2f)",
                action, side, exchange_qty, qty, self.position_size_usdt, self._last_price,
            )
            if limit:
                self._connector.submit_limit_order(
                    side=side, qty=exchange_qty, price=limit, reduce_only=reduce_only
                )
            else:
                self._connector.submit_market_order(
                    side=side, qty=exchange_qty, reduce_only=reduce_only
                )
        except Exception:
            logger.exception(
                "Order submission failed: %s %s qty=%.6f", action, side, exchange_qty
            )

    def on_entry(self, order: Order) -> None:
        """Called when Pine interpreter places a strategy.entry() order."""
        rec = self._record(order)
        self.signals.append(rec)

        direction = order.direction.value  # "long" or "short"

        # If reversing position, close first
        if self._position_side and self._position_side != direction:
            logger.info(
                "CLOSE %s position (reversal) | qty=%.6f",
                self._position_side.upper(),
                self._position_qty,
            )
            if self._demo_tracker:
                self._demo_tracker.on_close(self._last_price)
            self._submit_order(
                side="sell" if self._position_side == "long" else "buy",
                qty=self._position_qty,
                action="close",
            )
            self._position_side = None
            self._position_qty = 0.0

        logger.info(
            "ENTRY %s | id=%s qty=%.6f%s%s",
            direction.upper(),
            order.id,
            order.qty or 0,
            f" limit={order.limit}" if order.limit else "",
            f" stop={order.stop}" if order.stop else "",
        )

        self._position_side = direction
        self._position_qty = order.qty or 0.0

        if self._demo_tracker:
            self._demo_tracker.on_entry(direction, self._last_price)

        self._submit_order(
            side="buy" if direction == "long" else "sell",
            qty=order.qty or 0.0,
            action="entry",
            limit=order.limit,
            stop=order.stop,
        )

    def on_close(self, order: Order) -> None:
        """Called when Pine interpreter places a strategy.close() order."""
        rec = self._record(order)
        self.signals.append(rec)

        logger.info(
            "CLOSE %s | id=%s",
            self._position_side or "FLAT",
            order.id,
        )

        if self._position_side:
            if self._demo_tracker:
                self._demo_tracker.on_close(self._last_price)
            self._submit_order(
                side="sell" if self._position_side == "long" else "buy",
                qty=self._position_qty,
                action="close",
            )

        self._position_side = None
        self._position_qty = 0.0

    def on_exit(self, order: Order) -> None:
        """Called when Pine interpreter places a strategy.exit() order."""
        rec = self._record(order)
        self.signals.append(rec)

        logger.info(
            "EXIT %s | id=%s stop=%s limit=%s",
            self._position_side or "FLAT",
            order.id,
            order.stop,
            order.limit,
        )

        if self._position_side:
            self._submit_order(
                side="sell" if self._position_side == "long" else "buy",
                qty=order.qty or self._position_qty,
                action="exit",
                limit=order.limit,
                stop=order.stop,
            )

    # --- Helpers ---

    def update_price(self, price: float) -> None:
        """Update the current market price for P&L tracking."""
        self._last_price = price

    @property
    def demo_tracker(self) -> DemoTracker | None:
        return self._demo_tracker

    @staticmethod
    def _record(order: Order) -> SignalRecord:
        return SignalRecord(
            order_id=order.id,
            action=order.action,
            direction=order.direction.value,
            qty=order.qty or 0.0,
            limit=order.limit,
            stop=order.stop,
            comment=order.comment,
            bar_index=order.bar_index,
        )
