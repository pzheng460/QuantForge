"""
Base class for quantitative trading strategies on Bitget.

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
from nexustrader.exchange import BitgetAccountType
from nexustrader.indicator import Indicator
from nexustrader.schema import Kline, Order
from nexustrader.strategy import Strategy

from strategy.live.common.performance import PerformanceTracker


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
# Signal enum — imported from the indicator layer.
# Re-defined here so the base class can reference it without depending on
# any specific strategy's indicator module.
# ---------------------------------------------------------------------------

# We use string values that match indicator.Signal for duck-typing comparison.
# The actual Signal enum comes from each strategy's indicator module; the base
# class only compares against these sentinel string values.
_HOLD = "hold"
_BUY = "buy"
_SELL = "sell"
_CLOSE = "close"


# ---------------------------------------------------------------------------
# BaseQuantStrategy
# ---------------------------------------------------------------------------


class BaseQuantStrategy(Strategy):
    """Common base for all Bitget quantitative trading strategies.

    Handles:
    - Position state tracking
    - Daily stats & circuit breaker
    - Performance tracking
    - Post-warmup settle guard
    - Signal → order execution (pure mapping, no position management logic)
    - All order callbacks

    Subclasses override ``on_start()`` to subscribe symbols and create
    indicators, and optionally ``_format_log_line()`` for custom logging.
    """

    # Seconds to wait after warmup completes before allowing trades.
    _WARMUP_SETTLE_S: float = 5.0

    # ---------- Initialisation helpers ----------

    def _init_common(
        self,
        symbols: List[str],
        config,
        filter_config,
    ) -> None:
        """Initialise all common state.  Call from ``__init__``."""
        self._config = config
        self._filter = filter_config
        self._symbols = symbols

        # Per-symbol state
        self._indicators: Dict[str, Indicator] = {}
        self._positions: Dict[str, PositionState] = {}
        self._bar_count: Dict[str, int] = {}

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
            account_balance = self.cache.get_balance(BitgetAccountType.UTA_DEMO)
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

    # ---------- Signal execution (pure mapping) ----------

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

    # ---------- Order operations ----------

    def _open_position(self, symbol: str, side: OrderSide, price: float) -> None:
        """Open a new position."""
        amount = self._calculate_position_size(symbol, price)

        if amount <= 0:
            self.log.warning(f"Cannot open {side.value} position: amount too small")
            return

        self.log.info(
            f">>> OPENING {side.value} position: {symbol} @ {price:.2f}, size={amount}"
        )

        self.create_order(
            symbol=symbol,
            side=side,
            type=OrderType.MARKET,
            amount=amount,
        )

        position = self._positions[symbol]
        position.side = side
        position.entry_price = price
        position.amount = amount
        position.entry_time = datetime.now(timezone.utc)
        position.entry_bar = self._bar_count.get(symbol, 0)

        if self._performance:
            side_str = "long" if side == OrderSide.BUY else "short"
            self._performance.open_position(symbol, side_str, price, float(amount))

    def _close_position(self, symbol: str, reason: str) -> None:
        """Close existing position."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

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

        self._daily_stats.trade_count += 1

    def _close_all_positions(self, reason: str) -> None:
        """Close all open positions."""
        for symbol in self._symbols:
            self._close_position(symbol, reason)

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

    # ---------- Template on_kline ----------

    def on_kline(self, kline: Kline) -> None:
        """Common kline handler.  Processes warmup, settle, live-mode switch,
        signal execution, and periodic housekeeping."""
        symbol = kline.symbol
        price = float(kline.close)

        self._bar_count[symbol] = self._bar_count.get(symbol, 0) + 1
        current_bar = self._bar_count[symbol]

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
            return

        # Guard: skip trading until the post-warmup settle period has elapsed.
        if not self._check_live_ready(symbol):
            return

        # First activation of live mode
        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            indicator.enable_live_mode()
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

        signal = indicator.get_signal()
        position = self._positions.get(symbol)

        # Per-bar log line (strategy-specific)
        self.log.info(
            self._format_log_line(symbol, signal, position, indicator, current_bar)
        )

        # Periodic stats
        self._log_periodic_stats(symbol)

        # Daily housekeeping
        self._reset_daily_stats()
        self._init_performance_tracker()

        # Circuit breaker
        if self._check_circuit_breaker():
            self._close_all_positions("Circuit breaker active")
            return

        # Pure execution — signal already contains full position management
        self._execute_signal(symbol, signal, price)

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

    def on_partially_filled_order(self, order: Order) -> None:
        self.log.info(f"Order partially filled: {order}")

    def on_canceled_order(self, order: Order) -> None:
        self.log.info(f"Order canceled: {order}")

    def on_failed_order(self, order: Order) -> None:
        self.log.error(f"Order FAILED: {order}")
