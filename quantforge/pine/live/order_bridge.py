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

    initial_capital: float = 10_000.0  # fetched from exchange at startup
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

    def restore_trades(self, trades_data: list[dict]) -> None:
        """Restore trade history from serialized data (e.g. live_performance.json)."""
        from datetime import datetime

        for t in trades_data:
            entry_time = t.get("entry_time", "")
            exit_time = t.get("exit_time", "")
            # Parse ISO timestamps to epoch
            try:
                et = datetime.fromisoformat(entry_time).timestamp() if entry_time else 0.0
            except (ValueError, TypeError):
                et = 0.0
            try:
                xt = datetime.fromisoformat(exit_time).timestamp() if exit_time else 0.0
            except (ValueError, TypeError):
                xt = 0.0

            self.trades.append(VirtualTrade(
                direction=t.get("side", "long"),
                entry_price=t.get("entry_price", 0.0),
                exit_price=t.get("exit_price", 0.0),
                qty=t.get("amount", 0.0),
                pnl=t.get("pnl", 0.0),
                entry_time=et,
                exit_time=xt,
            ))
        if self.trades:
            logger.info(
                "Restored %d historical trades (total PnL: $%.2f)",
                len(self.trades), self.total_pnl,
            )

    def restore_position(self, side: str, entry_price: float, qty: float) -> None:
        """Restore an open position from exchange state."""
        self._position_side = side
        self._entry_price = entry_price
        self._position_qty = qty
        self._entry_time = time.time()  # approximate
        logger.info(
            "Restored open position: %s %.6f @ %.2f",
            side.upper(), qty, entry_price,
        )

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
            "📊 Demo P&L Summary",
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

        # Open position state for restart recovery
        open_position = None
        if self._position_side:
            open_position = {
                "side": self._position_side,
                "entry_price": self._entry_price,
                "qty": self._position_qty,
                "entry_time": datetime.fromtimestamp(
                    self._entry_time, tz=timezone.utc
                ).isoformat() if self._entry_time else "",
            }

        return {
            "start_time": start_str,
            "last_update": now_str,
            "mesa_index": 0,
            "config_name": "",
            "open_position": open_position,
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
    """Converts Pine strategy fills into real exchange orders.

    Pine has two callback phases for each order:

    * **Queue-time** (``on_entry`` / ``on_close`` / ``on_exit``): fires
      when ``strategy.entry/close/exit`` is called in the script. We
      record it as a SignalRecord for the web dashboard but do NOT
      submit anything — for entries the actual fill happens at the next
      bar's open, and for conditional exits possibly many bars later.

    * **Fill-time** (``on_entry_fill`` / ``on_close_fill``): fires from
      Pine's ``_execute_entry`` / ``_close_position`` when the order
      actually fills. We submit a real market order to the exchange here
      so the timing matches what the backtest sees. Pyramiding is
      handled correctly because every distinct entry fill flows through
      ``on_entry_fill`` with the qty Pine added.

    In ``demo=True`` (dry-run) mode the exchange path is skipped but the
    DemoTracker still records the virtual fills for web display.
    """

    def __init__(
        self,
        demo: bool = True,
        position_size_usdt: float = 100.0,
        leverage: int = 1,
        connector=None,
        symbol: str = "",
        initial_capital: float | None = None,
    ) -> None:
        self.demo = demo
        self.position_size_usdt = position_size_usdt
        self.leverage = leverage

        # Accumulated signal history (queue-time, for the dashboard).
        self.signals: list[SignalRecord] = []

        # CcxtConnector instance for real order submission
        self._connector = connector

        # External order submitter — legacy hook; signature:
        #   submit(symbol, side, qty, order_type, price=None)
        self._submit_fn = None

        # Current position view (mirrors Pine ``StrategyContext.position``
        # qty + direction). Updated by ``on_entry_fill`` / ``on_close_fill``.
        self._position_side: str | None = None
        self._position_qty: float = 0.0

        # Set of order ids already submitted to the exchange at queue-time.
        # When the matching fill callback fires later, the bridge skips
        # re-submission and only updates the local tracker. This is what
        # closes the 1-bar slippage gap for unconditional orders: Pine
        # queues at bar N close → bridge submits immediately at bar N
        # close + 5s → exchange fills at ~bar N+1 open, matching Pine's
        # backtest fill at execute_pending(bar=N+1).
        self._submitted_ids: set[str] = set()

        # Gate that turns off exchange submission while indicator state
        # is being backfilled (poll loop fell behind, replaying past
        # bars). Pine still runs through those bars to keep its internal
        # state current; we just don't send the "what should have fired"
        # orders to the exchange at wall-clock-stale prices. The engine
        # reconciles position with the exchange after backfill.
        self._submission_enabled: bool = True

        # P&L tracking (always enabled for web dashboard visibility)
        capital = initial_capital if initial_capital is not None else 10_000.0
        self._demo_tracker = DemoTracker(
            initial_capital=capital,
            symbol=symbol,
            position_size_usdt=position_size_usdt,
            leverage=leverage,
        )
        self._last_price: float = 0.0

    def sync_position(self, side: str | None, qty: float, entry_price: float) -> None:
        """Sync internal position state with external source (exchange or Pine).

        Call this after warmup to ensure OrderBridge matches the actual state.
        """
        self._position_side = side
        self._position_qty = qty
        self._last_price = entry_price if entry_price > 0 else self._last_price
        if self._demo_tracker and side:
            self._demo_tracker.restore_position(side, entry_price, qty)
        elif self._demo_tracker and not side:
            # Ensure tracker is flat too
            self._demo_tracker._position_side = None
            self._demo_tracker._position_qty = 0.0
        logger.info(
            "Position synced: %s qty=%.6f entry=%.2f",
            (side or "FLAT").upper(), qty, entry_price,
        )

    # ─── Queue-time callbacks ─────────────────────────────────────────
    #
    # Pine calls these from inside ``process_bar`` when the script invokes
    # ``strategy.entry/close/exit``. For UNCONDITIONAL orders (no stop /
    # limit / trail) we submit to the exchange IMMEDIATELY — that lands at
    # ~next-bar-open in market terms, matching backtest's
    # ``execute_pending(bar=N+1).fill_price = N+1.open``. The matching
    # fill-time callback later sees the id in ``_submitted_ids`` and skips
    # re-submission, only updating the local tracker.
    #
    # For CONDITIONAL exits (stop / limit / trail) we don't submit now —
    # Pine evaluates the trigger over many bars, and the fill-time
    # callback submits the market order when Pine actually fills.

    def _is_conditional(self, order: Order) -> bool:
        return (
            order.limit is not None
            or order.stop is not None
            or order.trail_points is not None
        )

    def _entry_qty_estimate(self, order: Order) -> float:
        """Estimate the resolved BTC qty for an entry at queue-time.

        In live the strategy_ctx is configured with ``default_qty_type=cash``
        and ``default_qty = position_size_usdt * leverage`` (see
        ``PineLiveEngine.start``), so the canonical sizing is
        ``notional / price``. Pine recomputes this at fill-time against
        the actual fill price; we use ``_last_price`` (≈ bar N close) as
        a proxy for bar N+1 open, accepting a tiny qty drift.
        """
        notional = self.position_size_usdt * self.leverage
        if self._last_price > 0:
            return notional / self._last_price
        # Without a price reference, fall back to the raw order qty (no-op).
        return order.qty or 0.0

    def on_entry(self, order: Order) -> None:
        """Pine queued a strategy.entry()."""
        self.signals.append(self._record(order))
        logger.info(
            "QUEUED ENTRY %s | id=%s%s%s%s",
            order.direction.value.upper(), order.id,
            f" limit={order.limit}" if order.limit else "",
            f" stop={order.stop}" if order.stop else "",
            f" trail={order.trail_points}" if order.trail_points else "",
        )
        if self._is_conditional(order):
            return  # exchange submission deferred to fill-time

        direction = order.direction.value
        new_qty = self._entry_qty_estimate(order)
        if new_qty <= 0:
            return

        # Reversal: in one-way mode the exchange handles {close + open} as
        # a single net market order. We submit qty = current + new and let
        # the exchange flip direction in one fill. Pine's internal ledger
        # still records two events (close + open) and the matching fill
        # callbacks update DemoTracker accordingly — both ids are marked
        # submitted so neither fires a second exchange order.
        if self._position_side and self._position_side != direction:
            total = self._position_qty + new_qty
            side = "buy" if direction == "long" else "sell"
            self._submit_market(side, total, action="reverse")
            self._submitted_ids.add(f"{order.id}__reverse")  # the close half
            self._submitted_ids.add(order.id)  # the new entry
            return

        side = "buy" if direction == "long" else "sell"
        self._submit_market(side, new_qty, action="entry")
        self._submitted_ids.add(order.id)

    def on_close(self, order: Order) -> None:
        """Pine queued a strategy.close() — always unconditional."""
        self.signals.append(self._record(order))
        logger.info(
            "QUEUED CLOSE %s | id=%s",
            self._position_side or "FLAT", order.id,
        )
        if not self._position_side or self._position_qty <= 0:
            return  # nothing to close
        side = "sell" if self._position_side == "long" else "buy"
        self._submit_market(side, self._position_qty, action="close")
        self._submitted_ids.add(order.id)

    def on_exit(self, order: Order) -> None:
        """Pine queued a strategy.exit() — always conditional."""
        self.signals.append(self._record(order))
        logger.info(
            "QUEUED EXIT %s | id=%s stop=%s limit=%s trail=%s",
            self._position_side or "FLAT", order.id,
            order.stop, order.limit, order.trail_points,
        )
        # Conditional — wait for Pine to fire the fill callback when bar
        # range crosses the trigger. No submission here.

    # ─── Fill-time callbacks (real exchange submission) ─────────────────
    #
    # Pine calls these from ``_execute_entry`` / ``_close_position`` —
    # at the moment the order actually fills in Pine's ledger. We mirror
    # the fill to the real exchange via a market order. Pyramiding is
    # supported because each Pine-side entry fill flows through here
    # with the incremental qty (Pine averages internally).

    def on_entry_fill(
        self, direction: str, price: float, qty: float, order_id: str = "",
    ) -> None:
        """Pine filled an entry. Submit only if not pre-submitted at queue-time."""
        if qty <= 0:
            return
        if price > 0:
            self._last_price = price

        # Pyramiding-aware position view (matches Pine internal averaging).
        if self._position_side == direction:
            self._position_qty += qty
        else:
            self._position_side = direction
            self._position_qty = qty

        already = order_id in self._submitted_ids
        if already:
            self._submitted_ids.discard(order_id)
            # Already submitted at queue-time; just record into DemoTracker.
            fill = price
        else:
            # Conditional entry that wasn't pre-submitted — submit now.
            side = "buy" if direction == "long" else "sell"
            result = self._submit_market(side, qty, action="entry")
            fill = self._extract_fill_price(result) or price

        if self._demo_tracker:
            if self._demo_tracker._position_side == direction:
                # accumulate qty + recompute avg entry
                old_qty = self._demo_tracker._position_qty
                old_entry = self._demo_tracker._entry_price
                new_total = old_qty + qty
                if new_total > 0:
                    self._demo_tracker._position_qty = new_total
                    self._demo_tracker._entry_price = (
                        (old_entry * old_qty + fill * qty) / new_total
                    )
            else:
                self._demo_tracker.on_entry(direction, fill)
                # Override qty DemoTracker would have estimated — Pine's
                # qty (the resolved one from execute_pending) is authoritative.
                self._demo_tracker._position_qty = qty

    def on_close_fill(
        self, direction: str, price: float, qty: float, order_id: str = "",
    ) -> None:
        """Pine filled a close. Submit only if not pre-submitted at queue-time."""
        if qty <= 0:
            return
        if price > 0:
            self._last_price = price

        # Update OrderBridge's view BEFORE submitting so a reversal's
        # subsequent entry sees flat state.
        self._position_qty = max(0.0, self._position_qty - qty)
        if self._position_qty <= 0:
            self._position_side = None

        already = order_id in self._submitted_ids
        if already:
            self._submitted_ids.discard(order_id)
            fill = price
        else:
            # Conditional exit fired by Pine — submit close now.
            side = "sell" if direction == "long" else "buy"
            result = self._submit_market(side, qty, action="close")
            fill = self._extract_fill_price(result) or price

        if self._demo_tracker and self._position_qty <= 0:
            self._demo_tracker.on_close(fill)

    # ─── Exchange submission helper ────────────────────────────────────

    def _submit_market(self, side: str, qty: float, action: str) -> dict | None:
        """Submit a market order to the exchange.

        Pine has already produced the correct ``qty`` (the live engine
        injects ``default_qty_type='cash'`` so Pine computes notional/price
        directly — see ``PineLiveEngine.start``). The connector handles
        reduce_only for closes.
        """
        if not self._submission_enabled:
            logger.debug(
                "Submission suppressed (backfill mode): %s %s qty=%.6f",
                action, side, qty,
            )
            return None
        if self.demo:
            return None
        if self._submit_fn:
            self._submit_fn(
                side=side, qty=qty, action=action, limit=None, stop=None,
            )
            return None
        if self._connector is None:
            logger.warning("No connector configured — %s order not submitted", action)
            return None
        if qty <= 0:
            logger.warning("Refusing to submit qty<=0 (%s %s)", action, side)
            return None
        try:
            reduce_only = action in ("close", "exit")
            logger.info(
                "Exchange order: %s %s qty=%.6f reduce_only=%s",
                action, side, qty, reduce_only,
            )
            return self._connector.submit_market_order(
                side=side, qty=qty, reduce_only=reduce_only,
            )
        except Exception:
            logger.exception(
                "Order submission failed: %s %s qty=%.6f", action, side, qty
            )
            return None

    # --- Helpers ---

    def _extract_fill_price(self, result: dict | None) -> float:
        """Extract the average fill price from a ccxt order result.

        Falls back to ``self._last_price`` if the result is unavailable or
        does not contain a valid average price.
        """
        if result is not None:
            avg = result.get("average")
            if avg is not None:
                try:
                    price = float(avg)
                    if price > 0:
                        return price
                except (TypeError, ValueError):
                    pass
        return self._last_price

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
