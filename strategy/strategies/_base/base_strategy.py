"""
Base class for quantitative trading strategies.

Extracts common code shared by all live trading strategies:
- LogTee: dual-write to stdout + log file
- PositionState / DailyStats: position and stats tracking
- BaseQuantStrategy: lifecycle, signal execution, order management,
  performance tracking, circuit breaker, and all order callbacks.

Subclasses only need to implement:
- on_start()              — subscribe symbols, create indicators, register
- _format_log_line()      — (optional) customise per-bar log output
"""

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from nexustrader.constants import (
    DataType,
    KlineInterval,
    OrderSide,
    OrderType,
)
from nexustrader.indicator import Indicator
from nexustrader.schema import Kline, Order
from nexustrader.strategy import Strategy

from strategy.strategies._base.performance import PerformanceTracker


# ---------------------------------------------------------------------------
# Utility: dual-write stdout + log file
# ---------------------------------------------------------------------------


class LogTee:
    """Writes output to both stdout and a log file."""

    def __init__(self, filename: str):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()

    def close(self) -> None:
        self.log.close()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PositionState:
    """Track position state for a symbol."""

    symbol: str
    side: Optional[OrderSide] = None
    entry_price: float = 0.0
    amount: Decimal = Decimal("0")
    entry_time: Optional[datetime] = None
    entry_bar: int = 0


@dataclass
class DailyStats:
    """Track daily trading statistics."""

    date: str = ""
    starting_balance: float = 0.0
    realized_pnl: float = 0.0
    trade_count: int = 0
    is_circuit_breaker_active: bool = False


# ---------------------------------------------------------------------------
# Signal sentinel values — used for duck-typing comparison with each
# strategy's Signal enum .value attribute.
# ---------------------------------------------------------------------------

_HOLD = "hold"
_BUY = "buy"
_SELL = "sell"
_CLOSE = "close"


# ---------------------------------------------------------------------------
# BaseQuantStrategy
# ---------------------------------------------------------------------------


class BaseQuantStrategy(Strategy):
    """Common base for all quantitative trading strategies.

    Handles:
    - Position state tracking
    - Signal filtering (confirmation, cooldown, min holding)
    - Daily stats & circuit breaker
    - Performance tracking
    - Post-warmup settle guard
    - Stale data guard (opt-in)
    - Stop loss detection
    - Signal → order execution
    - All order callbacks

    Subclasses override ``on_start()`` to subscribe symbols and create
    indicators, and optionally ``_format_log_line()`` for custom logging.

    Hook methods for subclass customisation:
    - ``_on_live_activated()``   — called once when live trading starts
    - ``_get_signal()``          — override to pass custom args to indicator
    - ``_check_stop_loss()``     — override for custom stop loss logic
    - ``_pre_signal_hook()``     — called before ``_process_signal()``
    - ``_process_signal()``      — override for custom signal handling
    """

    # Seconds to wait after warmup completes before allowing trades.
    _WARMUP_SETTLE_S: float = 5.0

    # Stale data guard (opt-in via subclass)
    _ENABLE_STALE_GUARD: bool = False
    _MAX_KLINE_AGE_S: float = 120.0

    # ---------- Initialisation helpers ----------

    def _init_common(
        self,
        symbols: List[str],
        config,
        filter_config,
        *,
        account_type,
    ) -> None:
        """Initialise all common state.  Call from ``__init__``."""
        self._account_type = account_type
        self._config = config
        self._filter = filter_config
        self._symbols = symbols

        # Per-symbol state
        self._indicators: Dict[str, Indicator] = {}
        self._positions: Dict[str, PositionState] = {}
        self._bar_count: Dict[str, int] = {}

        # Pre-order state snapshots for rollback on order failure
        self._pre_order_snapshots: Dict[str, dict] = {}

        # Signal filtering state
        self._cooldown_until: Dict[str, int] = {}
        self._signal_history: Dict[str, List] = {}

        # Daily statistics
        self._daily_stats = DailyStats()

        # Performance tracker (initialised when balance available)
        self._performance: Optional[PerformanceTracker] = None
        self._performance_initialized: bool = False
        self._mesa_index: int = 0
        self._config_name: str = ""

        # Stats logging interval (every N bars)
        self._stats_log_interval: int = 4  # Every hour (4 × 15 min)
        self._last_stats_bar: int = 0

        # Live-mode guard
        self._warmup_done_at: Dict[str, float] = {}
        self._live_trading_ready: Dict[str, bool] = {}

        # Stale-data burst detection
        self._stale_burst_count: Dict[str, int] = {}
        self._burst_settle_until: Dict[str, float] = {}

    # ---------- Registration helper ----------

    def _register_symbol(
        self,
        symbol: str,
        indicator: Indicator,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ) -> None:
        """Register a symbol with its indicator and subscriptions."""
        self._indicators[symbol] = indicator
        self._positions[symbol] = PositionState(symbol=symbol)
        self._bar_count[symbol] = 0

        self.subscribe_kline(symbol, kline_interval)
        self.subscribe_bookl1(symbol)

        self.register_indicator(
            symbols=symbol,
            indicator=indicator,
            data_type=DataType.KLINE,
        )

    def set_config_info(self, mesa_index: int, name: str) -> None:
        """Set config info for performance tracking."""
        self._mesa_index = mesa_index
        self._config_name = name

    # ---------- Signal filtering utilities ----------

    def _is_signal_confirmed(self, symbol: str, signal) -> bool:
        """Check if signal has enough consecutive confirmations."""
        history = self._signal_history.get(symbol, [])
        if len(history) < self._filter.signal_confirmation:
            return False
        for i in range(1, self._filter.signal_confirmation + 1):
            if history[-i] != signal:
                return False
        return True

    def _is_in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown period."""
        current_bar = self._bar_count.get(symbol, 0)
        cooldown_until = self._cooldown_until.get(symbol, 0)
        return current_bar < cooldown_until

    def _can_close_position(self, symbol: str) -> bool:
        """Check if position can be closed (min holding period)."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return False
        current_bar = self._bar_count.get(symbol, 0)
        bars_held = current_bar - position.entry_bar
        return bars_held >= self._filter.min_holding_bars

    # ---------- Stale data guard ----------

    def _is_kline_stale(self, kline) -> bool:
        """Check if a kline is stale (too old compared to wall-clock time)."""
        now_ms = int(time.time() * 1000)
        kline_age_s = (now_ms - kline.timestamp) / 1000.0
        return kline_age_s > self._MAX_KLINE_AGE_S

    def _check_stale_data(self, symbol: str, kline) -> bool:
        """Check for stale data burst. Returns True if kline should be skipped.

        No-op if ``_ENABLE_STALE_GUARD`` is False.
        """
        if not self._ENABLE_STALE_GUARD:
            return False

        is_stale = self._is_kline_stale(kline)

        if is_stale:
            prev_count = self._stale_burst_count.get(symbol, 0)
            self._stale_burst_count[symbol] = prev_count + 1
            if self._stale_burst_count[symbol] == 1:
                self.log.warning(
                    f"{symbol} | STALE DATA detected (kline age > {self._MAX_KLINE_AGE_S}s), "
                    f"skipping trades until live data resumes"
                )
            return True

        # If we just exited a stale burst, enforce a short settle period.
        if self._stale_burst_count.get(symbol, 0) > 0:
            burst_size = self._stale_burst_count[symbol]
            self.log.info(
                f"{symbol} | Stale burst ended ({burst_size} bars skipped), "
                f"settling for {self._WARMUP_SETTLE_S}s before trading"
            )
            self._stale_burst_count[symbol] = 0
            self._burst_settle_until[symbol] = time.monotonic() + self._WARMUP_SETTLE_S
            # Clear signal history — it's contaminated by stale data.
            self._signal_history[symbol] = []

        # If still in post-burst settle period, skip trading.
        settle_deadline = self._burst_settle_until.get(symbol, 0.0)
        if settle_deadline > 0.0 and time.monotonic() < settle_deadline:
            return True

        return False

    # ---------- Lifecycle ----------

    def _reset_daily_stats(self) -> None:
        """Reset daily statistics at UTC midnight."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_stats.date != today:
            balance = self._get_account_balance()
            self._daily_stats = DailyStats(
                date=today,
                starting_balance=balance if balance > 0 else 10000.0,
            )
            self.log.info(f"Reset daily stats for {today}")

    def _get_account_balance(self) -> float:
        """Get current account balance in USDT."""
        try:
            account_balance = self.cache.get_balance(self._account_type)
            if account_balance and "USDT" in account_balance.balances:
                return float(account_balance.balances["USDT"].total)
        except Exception as e:
            self.log.warning(f"Failed to get balance: {e}")
        return 0.0

    def _init_performance_tracker(self) -> bool:
        """Initialise performance tracker when balance is available."""
        if self._performance_initialized:
            return True

        balance = self._get_account_balance()
        if balance <= 0:
            return False

        self._performance = PerformanceTracker(
            initial_balance=balance,
            mesa_index=self._mesa_index,
            config_name=self._config_name,
        )
        self._performance_initialized = True
        self.log.info("=" * 60)
        self.log.info("Performance Tracker initialized!")
        self.log.info(f"  Initial Balance: {balance:,.2f} USDT")
        self.log.info(f"  Config: Mesa #{self._mesa_index} ({self._config_name})")
        self.log.info("=" * 60)
        return True

    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be triggered."""
        if self._daily_stats.is_circuit_breaker_active:
            return True

        current_balance = self._get_account_balance()
        starting_balance = self._daily_stats.starting_balance

        if starting_balance <= 0:
            return False

        daily_pnl_pct = (current_balance - starting_balance) / starting_balance

        if daily_pnl_pct < -self._config.daily_loss_limit:
            self.log.warning(
                f"CIRCUIT BREAKER TRIGGERED: Daily loss {daily_pnl_pct * 100:.2f}% "
                f"exceeds limit {self._config.daily_loss_limit * 100:.2f}%"
            )
            self._daily_stats.is_circuit_breaker_active = True
            return True

        return False

    def _check_live_ready(self, symbol: str) -> bool:
        """Return True once the post-warmup settle period has elapsed."""
        if self._live_trading_ready.get(symbol, False):
            return True
        done_at = self._warmup_done_at.get(symbol)
        if done_at is None:
            return False
        if time.monotonic() - done_at >= self._WARMUP_SETTLE_S:
            self._live_trading_ready[symbol] = True
            return True
        return False

    def _calculate_position_size(self, symbol: str, price: float) -> Decimal:
        """Calculate position size based on account balance."""
        balance = self._get_account_balance()
        position_value = balance * self._config.position_size_pct
        amount = position_value / price
        return self.amount_to_precision(symbol, Decimal(str(amount)))

    def _get_core_position(self, symbol: str) -> int:
        """Return the core's internal position int (0/1/-1), or 0 if not applicable."""
        indicator = self._indicators.get(symbol)
        if indicator and hasattr(indicator, "core") and hasattr(indicator.core, "position"):
            return indicator.core.position
        return 0

    # ---------- Order operations ----------

    def _open_position(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        current_bar: Optional[int] = None,
    ) -> None:
        """Open a new position."""
        amount = self._calculate_position_size(symbol, price)

        if amount <= 0:
            self.log.warning(f"Cannot open {side.value} position: amount too small")
            return

        # Snapshot state before firing order (for rollback on failure)
        position = self._positions[symbol]
        self._pre_order_snapshots[symbol] = {
            "side": position.side,
            "entry_price": position.entry_price,
            "amount": position.amount,
            "entry_time": position.entry_time,
            "entry_bar": position.entry_bar,
            "core_pos": self._get_core_position(symbol),
        }

        self.log.info(
            f">>> OPENING {side.value} position: {symbol} @ {price:.2f}, size={amount}"
        )

        self.create_order(
            symbol=symbol,
            side=side,
            type=OrderType.MARKET,
            amount=amount,
        )

        position.side = side
        position.entry_price = price
        position.amount = amount
        position.entry_time = datetime.now(timezone.utc)
        position.entry_bar = (
            current_bar if current_bar is not None else self._bar_count.get(symbol, 0)
        )

        if self._performance:
            side_str = "long" if side == OrderSide.BUY else "short"
            self._performance.open_position(symbol, side_str, price, float(amount))

    def _close_position(self, symbol: str, reason: str, force: bool = False) -> None:
        """Close existing position."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

        if not force and not self._can_close_position(symbol):
            return

        # Snapshot state before firing order (for rollback on failure)
        self._pre_order_snapshots[symbol] = {
            "side": position.side,
            "entry_price": position.entry_price,
            "amount": position.amount,
            "entry_time": position.entry_time,
            "entry_bar": position.entry_bar,
            "core_pos": self._get_core_position(symbol),
        }

        self.log.info(f"<<< CLOSING position: {symbol}, reason={reason}")

        exit_price = position.entry_price
        bookl1 = self.cache.bookl1(symbol)
        if bookl1:
            exit_price = float(
                bookl1.bid if position.side == OrderSide.BUY else bookl1.ask
            )

        if self._performance:
            trade = self._performance.close_position(exit_price, reason)
            if trade:
                self.log.info(
                    f"Trade recorded: P&L = {trade.pnl:+.2f} USDT ({trade.pnl_pct:+.2f}%)"
                )
                current_balance = self._get_account_balance()
                self._performance.update_balance(current_balance)

        close_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY

        self.create_order(
            symbol=symbol,
            side=close_side,
            type=OrderType.MARKET,
            amount=position.amount,
        )

        position.side = None
        position.entry_price = 0.0
        position.amount = Decimal("0")
        position.entry_time = None
        position.entry_bar = 0

        # Set cooldown
        current_bar = self._bar_count.get(symbol, 0)
        self._cooldown_until[symbol] = current_bar + self._filter.cooldown_bars

        self._daily_stats.trade_count += 1

    def _close_all_positions(self, reason: str) -> None:
        """Close all open positions."""
        for symbol in self._symbols:
            self._close_position(symbol, reason, force=True)

    # ---------- Signal execution ----------

    def _execute_signal(self, symbol: str, signal, price: float) -> None:
        """Execute a fully-managed signal from core.update().

        ``signal`` is a Signal enum with ``.value`` in {hold, buy, sell, close}.
        No position management decisions happen here — core already handles
        stop loss, cooldown, signal confirmation, and min holding.
        """
        position = self._positions.get(symbol)
        if not position:
            return

        sig = signal.value  # compare by string

        if sig == _BUY and position.side is None:
            self._open_position(symbol, OrderSide.BUY, price)
        elif sig == _SELL and position.side is None:
            self._open_position(symbol, OrderSide.SELL, price)
        elif sig == _CLOSE and position.side is not None:
            self._close_position(symbol, "Signal close")

    def _process_signal(
        self, symbol: str, signal, price: float, current_bar: int
    ) -> None:
        """Default raw signal handler with confirmation + cooldown + reversal.

        Handles the common pattern used by bollinger_band, vwap, hurst_kalman,
        regime_ema, and momentum.  Subclasses may override for different
        behaviour (e.g. short-only for funding_rate, managed mode for ema_crossover).
        """
        position = self._positions.get(symbol)
        if not position:
            return

        sig = signal.value

        if self._is_in_cooldown(symbol):
            return

        if sig in (_BUY, _SELL) and not self._is_signal_confirmed(symbol, signal):
            return

        if sig == _CLOSE:
            if position.side is not None and self._can_close_position(symbol):
                self._close_position(symbol, "Signal close")
            return

        if sig == _BUY:
            if position.side == OrderSide.SELL and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to long")
            if position.side is None:
                self._open_position(symbol, OrderSide.BUY, price, current_bar)

        elif sig == _SELL:
            if position.side == OrderSide.BUY and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to short")
            if position.side is None:
                self._open_position(symbol, OrderSide.SELL, price, current_bar)

    # ---------- Periodic stats logging ----------

    def _log_periodic_stats(self, symbol: str) -> None:
        """Log performance stats at regular intervals."""
        current_bar = self._bar_count.get(symbol, 0)
        if (
            self._performance
            and current_bar - self._last_stats_bar >= self._stats_log_interval
        ):
            self._last_stats_bar = current_bar
            current_balance = self._get_account_balance()
            if current_balance > 0:
                self._performance.update_balance(current_balance)
                stats = self._performance.get_stats()
                self.log.info(
                    f"[PERF] Balance: {stats.current_balance:,.2f} | "
                    f"Return: {stats.total_return_pct:+.2f}% | "
                    f"Trades: {stats.total_trades} ({stats.win_rate_pct:.1f}% win) | "
                    f"MaxDD: {stats.max_drawdown_pct:.2f}%"
                )

    # ---------- Subclass hooks ----------

    def _format_log_line(
        self,
        symbol: str,
        signal,
        position: PositionState,
        indicator: Indicator,
        current_bar: int,
    ) -> str:
        """Format per-bar log line.  Override in subclasses for strategy-specific fields."""
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position.side
            else "FLAT"
        )
        return f"{symbol} | Bar={current_bar} | Signal={signal.value} | Pos={pos_str}"

    def _on_live_activated(
        self, symbol: str, indicator: Indicator, current_bar: int
    ) -> None:
        """Called once when live trading activates.  Override for enable_live_mode(), etc."""
        self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

    def _get_signal(self, symbol: str, indicator: Indicator):
        """Get signal from indicator.  Override to pass custom args."""
        return indicator.get_signal()

    def _check_stop_loss(self, symbol: str, indicator: Indicator, price: float) -> bool:
        """Check stop loss.  Returns True if stopped.

        Override for different stop loss signatures.
        """
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return False
        is_long = position.side == OrderSide.BUY
        if hasattr(indicator, "should_stop_loss"):
            if indicator.should_stop_loss(position.entry_price, price, is_long=is_long):
                self.log.warning(f"STOP LOSS triggered for {symbol}")
                self._close_position(symbol, "Stop loss", force=True)
                return True
        return False

    def _pre_signal_hook(
        self, symbol: str, signal, price: float, indicator: Indicator, current_bar: int
    ) -> bool:
        """Called before _process_signal.  Return True to skip signal processing."""
        return False

    # ---------- Template on_kline ----------

    def on_kline(self, kline: Kline) -> None:
        """Common kline handler.  Processes warmup, settle, live-mode switch,
        signal execution, and periodic housekeeping."""
        symbol = kline.symbol
        price = float(kline.close)

        self._bar_count[symbol] = self._bar_count.get(symbol, 0) + 1
        current_bar = self._bar_count[symbol]

        self._reset_daily_stats()
        self._init_performance_tracker()

        indicator = self._indicators.get(symbol)
        if not indicator:
            return

        indicator.handle_kline(kline)

        if not indicator.is_warmed_up:
            if current_bar % 10 == 0:
                self.log.info(f"{symbol} | Warming up... bar {current_bar}")
            return

        # Record the wall-clock moment warmup completed (once per symbol).
        if symbol not in self._warmup_done_at:
            self._warmup_done_at[symbol] = time.monotonic()
            self.log.info(
                f"{symbol} | Warmup complete at bar {current_bar}, settling..."
            )

        # Guard: skip trading until the post-warmup settle period has elapsed.
        if not self._check_live_ready(symbol):
            return

        # First activation of live mode
        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            self._on_live_activated(symbol, indicator, current_bar)

        # Stale data guard (opt-in)
        if self._check_stale_data(symbol, kline):
            return

        # Get signal
        signal = self._get_signal(symbol, indicator)

        # Signal history
        if symbol not in self._signal_history:
            self._signal_history[symbol] = []
        self._signal_history[symbol].append(signal)
        if len(self._signal_history[symbol]) > 10:
            self._signal_history[symbol] = self._signal_history[symbol][-10:]

        position = self._positions.get(symbol)

        # Log
        self.log.info(
            self._format_log_line(symbol, signal, position, indicator, current_bar)
        )

        # Periodic stats
        self._log_periodic_stats(symbol)

        # Circuit breaker
        if self._check_circuit_breaker():
            self._close_all_positions("Circuit breaker active")
            return

        if not position:
            return

        # Stop loss (always allowed, force=True)
        if self._check_stop_loss(symbol, indicator, price):
            return

        # Pre-signal hook
        if self._pre_signal_hook(symbol, signal, price, indicator, current_bar):
            return

        # Process signal
        self._process_signal(symbol, signal, price, current_bar)

    # ---------- Order callbacks ----------

    def on_pending_order(self, order: Order) -> None:
        self.log.debug(f"Order pending: {order}")

    def on_accepted_order(self, order: Order) -> None:
        self.log.info(f"Order accepted: {order.symbol} {order.side} {order.amount}")

    def on_filled_order(self, order: Order) -> None:
        self.log.info(
            f"Order FILLED: {order.symbol} {order.side} {order.amount} @ {order.price}"
        )
        self._daily_stats.trade_count += 1
        # Clear snapshot — order succeeded, no rollback needed
        self._pre_order_snapshots.pop(order.symbol, None)

    def on_partially_filled_order(self, order: Order) -> None:
        self.log.info(f"Order partially filled: {order}")

    def on_canceled_order(self, order: Order) -> None:
        self.log.info(f"Order canceled: {order}")

    def on_failed_order(self, order: Order) -> None:
        self.log.error(f"Order FAILED: {order}")
        symbol = order.symbol
        snapshot = self._pre_order_snapshots.pop(symbol, None)
        if snapshot is None:
            return

        # Rollback strategy position state
        position = self._positions.get(symbol)
        if position:
            position.side = snapshot["side"]
            position.entry_price = snapshot["entry_price"]
            position.amount = snapshot["amount"]
            position.entry_time = snapshot["entry_time"]
            position.entry_bar = snapshot["entry_bar"]
            self.log.warning(
                f"{symbol} | Order failed — rolled back position state to {snapshot['side']}"
            )

        # Rollback core position state (dual-mode strategies)
        indicator = self._indicators.get(symbol)
        if indicator and hasattr(indicator, "core"):
            core = indicator.core
            if hasattr(core, "sync_position"):
                core.sync_position(snapshot["core_pos"], snapshot["entry_price"])
                self.log.warning(
                    f"{symbol} | Order failed — rolled back core.position to {snapshot['core_pos']}"
                )
