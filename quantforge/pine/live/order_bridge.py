"""Bridges Pine strategy signals to real exchange orders.

When the Pine interpreter calls ``strategy.entry`` / ``strategy.close`` /
``strategy.exit``, the :class:`OrderBridge` translates those into exchange
order submissions via the QuantForge connector layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
    ) -> None:
        self.demo = demo
        self.position_size_usdt = position_size_usdt

        # Accumulated signal history
        self.signals: list[SignalRecord] = []

        # External order submitter — set by the live engine / connector layer.
        # Signature: async submit(symbol, side, qty, order_type, price=None)
        self._submit_fn = None

        # Current position tracking (mirrors Pine StrategyContext)
        self._position_side: str | None = None  # "long" / "short" / None
        self._position_qty: float = 0.0

    # --- Callbacks wired into StrategyContext ---

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
            if not self.demo and self._submit_fn:
                self._submit_fn(
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

        if not self.demo and self._submit_fn:
            self._submit_fn(
                side="buy" if direction == "long" else "sell",
                qty=order.qty,
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

        if not self.demo and self._submit_fn and self._position_side:
            self._submit_fn(
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

        if not self.demo and self._submit_fn and self._position_side:
            self._submit_fn(
                side="sell" if self._position_side == "long" else "buy",
                qty=order.qty or self._position_qty,
                action="exit",
                limit=order.limit,
                stop=order.stop,
            )

    # --- Helpers ---

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
