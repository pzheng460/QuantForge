"""
VWAP Mean Reversion Trading Strategy for Bitget.

This strategy uses:
- VWAP (Volume Weighted Average Price) with daily reset at 00:00 UTC
- Z-Score of price deviation from VWAP for entry/exit signals
- RSI (14) as secondary confirmation filter
- 5-minute candle timeframe

Usage:
    uv run python -m strategy.bitget.vwap.strategy

Log output:
    strategy/bitget/vwap/strategy_output.log
"""

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional


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


# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "strategy_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.config import (  # noqa: E402
    BasicConfig,
    Config,
    LogConfig,
    PrivateConnectorConfig,
    PublicConnectorConfig,
)
from nexustrader.constants import (  # noqa: E402
    DataType,
    ExchangeType,
    KlineInterval,
    OrderSide,
    OrderType,
    settings,
)
from nexustrader.engine import Engine  # noqa: E402
from nexustrader.exchange import BitgetAccountType  # noqa: E402
from nexustrader.schema import Kline, Order  # noqa: E402
from nexustrader.strategy import Strategy  # noqa: E402

from strategy.bitget.vwap.configs import (  # noqa: E402
    VWAPTradeFilterConfig,
    get_config,
)
from strategy.strategies.vwap.core import VWAPConfig  # noqa: E402
from strategy.bitget.vwap.indicator import (  # noqa: E402
    VWAPIndicator,
    Signal,
)
from strategy.bitget.common.performance import PerformanceTracker  # noqa: E402


# API credentials from settings
API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE


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


class VWAPMeanReversionStrategy(Strategy):
    """
    VWAP mean reversion quantitative trading strategy.

    Uses VWAP + Z-Score + RSI for mean-reversion signals:
    - Z <= -entry AND RSI < oversold: BUY (price below VWAP)
    - Z >= entry AND RSI > overbought: SELL (price above VWAP)
    - |Z| < exit_threshold: CLOSE (price returned to VWAP)
    - |Z| >= zscore_stop OR price > stop_loss_pct: STOP LOSS
    """

    # Seconds to wait after warmup completes before allowing trades.
    _WARMUP_SETTLE_S: float = 5.0

    # Maximum allowed age (in seconds) for a kline to be considered "live".
    # Klines older than this are treated as stale (e.g. from websocket reconnect
    # replaying historical data) and will update indicators but NOT trigger trades.
    _MAX_KLINE_AGE_S: float = 60.0


    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[VWAPConfig] = None,
        filter_config: Optional[VWAPTradeFilterConfig] = None,
    ):
        super().__init__()

        self._config = config or VWAPConfig()
        self._filter = filter_config or VWAPTradeFilterConfig()

        self._symbols = symbols or self._config.symbols

        # Indicators per symbol
        self._indicators: Dict[str, VWAPIndicator] = {}

        # Position tracking per symbol
        self._positions: Dict[str, PositionState] = {}

        # Daily statistics
        self._daily_stats = DailyStats()

        # Bar counter per symbol
        self._bar_count: Dict[str, int] = {}

        # Cooldown tracking (bar number when cooldown ends)
        self._cooldown_until: Dict[str, int] = {}

        # Signal history for confirmation
        self._signal_history: Dict[str, List[Signal]] = {}

        # Track pending orders
        self._pending_orders: Dict[str, str] = {}

        # Performance tracker (initialized in on_start)
        self._performance: Optional[PerformanceTracker] = None

        # Config info for performance tracking
        self._mesa_index: int = 0
        self._config_name: str = ""

        # Stats logging interval (every N bars)
        self._stats_log_interval: int = 12  # Every hour (12 * 5min)
        self._last_stats_bar: int = 0

        # Live-mode guard: wall-clock time when warmup completed per symbol.
        self._warmup_done_at: Dict[str, float] = {}
        self._live_trading_ready: Dict[str, bool] = {}

        # Stale-data burst detection: tracks consecutive stale bars after
        # live trading has been activated.  Resets to 0 on each fresh bar.
        self._stale_burst_count: Dict[str, int] = {}
        # Wall-clock settle delay after a stale burst ends.
        self._burst_settle_until: Dict[str, float] = {}

    def set_config_info(self, mesa_index: int, name: str) -> None:
        """Set config info for performance tracking."""
        self._mesa_index = mesa_index
        self._config_name = name

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting VWAP Mean Reversion Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  std_window: {self._config.std_window}")
        self.log.info(f"  rsi_period: {self._config.rsi_period}")
        self.log.info(f"  zscore_entry: {self._config.zscore_entry}")
        self.log.info(f"  zscore_exit: {self._config.zscore_exit}")
        self.log.info(f"  zscore_stop: {self._config.zscore_stop}")
        self.log.info(f"  rsi_oversold: {self._config.rsi_oversold}")
        self.log.info(f"  rsi_overbought: {self._config.rsi_overbought}")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100}%")
        self.log.info("Filter Config:")
        self.log.info(
            f"  min_holding_bars: {self._filter.min_holding_bars} ({self._filter.min_holding_bars * 5} min)"
        )
        self.log.info(
            f"  cooldown_bars: {self._filter.cooldown_bars} ({self._filter.cooldown_bars * 5} min)"
        )
        self.log.info(f"  signal_confirmation: {self._filter.signal_confirmation}")
        self.log.info("=" * 60)

        # Initialize daily stats
        self._reset_daily_stats()

        # Performance tracker will be initialized when we get first valid balance
        self._performance_initialized = False
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = VWAPIndicator(
                config=self._config,
                kline_interval=KlineInterval.MINUTE_5,
            )
            self._indicators[symbol] = indicator

            self._positions[symbol] = PositionState(symbol=symbol)
            self._bar_count[symbol] = 0
            self._cooldown_until[symbol] = 0
            self._signal_history[symbol] = []

            self.subscribe_kline(symbol, KlineInterval.MINUTE_5)
            self.subscribe_bookl1(symbol)

            self.register_indicator(
                symbols=symbol,
                indicator=indicator,
                data_type=DataType.KLINE,
            )

            self.log.info(f"Initialized tracking for {symbol}")

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
        """Initialize performance tracker when balance is available."""
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

    def _calculate_position_size(self, symbol: str, price: float) -> Decimal:
        """Calculate position size based on account balance."""
        balance = self._get_account_balance()
        position_value = balance * self._config.position_size_pct
        amount = position_value / price
        return self.amount_to_precision(symbol, Decimal(str(amount)))

    def _is_signal_confirmed(self, symbol: str, signal: Signal) -> bool:
        """Check if signal has enough consecutive confirmations."""
        history = self._signal_history.get(symbol, [])

        if len(history) < self._filter.signal_confirmation:
            return False

        for i in range(1, self._filter.signal_confirmation + 1):
            if history[-i] != signal:
                return False

        return True

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

    def _is_kline_stale(self, kline) -> bool:
        """Check if a kline is stale (too old compared to wall-clock time).

        This detects websocket reconnection bursts where the exchange replays
        many historical klines at once.  These should update indicators but
        must NOT trigger trades.
        """
        now_ms = int(time.time() * 1000)
        kline_age_s = (now_ms - kline.timestamp) / 1000.0
        return kline_age_s > self._MAX_KLINE_AGE_S

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

    def on_kline(self, kline: Kline) -> None:
        """Process new kline data."""
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

        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

        # ------------------------------------------------------------------
        # Stale-data guard: detect websocket reconnection bursts.
        # When the websocket reconnects, the exchange replays many historical
        # klines at once.  These klines have old timestamps and arrive in a
        # rapid burst.  We allow them to update indicators (so VWAP/RSI stay
        # correct) but we MUST NOT trade on them — the prices are historical
        # and no longer actionable.
        # ------------------------------------------------------------------
        is_stale = self._is_kline_stale(kline)

        if is_stale:
            prev_count = self._stale_burst_count.get(symbol, 0)
            self._stale_burst_count[symbol] = prev_count + 1
            if self._stale_burst_count[symbol] == 1:
                self.log.warning(
                    f"{symbol} | STALE DATA detected (kline age > {self._MAX_KLINE_AGE_S}s), "
                    f"skipping trades until live data resumes"
                )
            # Don't log every stale bar — just update indicators silently.
            return

        # If we just exited a stale burst, enforce a short settle period
        # to let indicators stabilize on fresh data before trading.
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
            return

        signal = indicator.get_signal()
        if symbol not in self._signal_history:
            self._signal_history[symbol] = []
        self._signal_history[symbol].append(signal)
        if len(self._signal_history[symbol]) > 10:
            self._signal_history[symbol] = self._signal_history[symbol][-10:]

        position = self._positions.get(symbol)
        bars_held = (
            current_bar - position.entry_bar if position and position.side else 0
        )

        cooldown_remaining = max(0, self._cooldown_until.get(symbol, 0) - current_bar)
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )

        vwap_str = f"{indicator.vwap:.1f}" if indicator.vwap else "N/A"

        self.log.info(
            f"{symbol} | Bar={current_bar} | VWAP={vwap_str} | Z={indicator.zscore:.2f} | "
            f"RSI={indicator.rsi:.1f} | Signal={signal.value} | Pos={pos_str} | "
            f"Hold={bars_held} | CD={cooldown_remaining}"
        )

        # Periodic performance stats logging
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

        # Check circuit breaker
        if self._check_circuit_breaker():
            self._close_all_positions("Circuit breaker active")
            return

        if not position:
            return

        # Check stop loss (always allowed, ignore min holding)
        if position.side is not None:
            is_long = position.side == OrderSide.BUY
            if indicator.should_stop_loss(position.entry_price, price, is_long=is_long):
                self.log.warning(f"STOP LOSS triggered for {symbol}")
                self._close_position(symbol, "Stop loss", force=True)
                return

        # Skip if in cooldown
        if self._is_in_cooldown(symbol):
            return

        # Process trading signal
        self._process_signal(symbol, signal, price, current_bar)

    def _process_signal(
        self,
        symbol: str,
        signal: Signal,
        price: float,
        current_bar: int,
    ) -> None:
        """Process trading signal and execute orders."""
        position = self._positions.get(symbol)
        if not position:
            return

        # Check signal confirmation for entries
        if signal in [Signal.BUY, Signal.SELL]:
            if not self._is_signal_confirmed(symbol, signal):
                return

        # Mean reversion close signal (price returned to VWAP)
        if signal == Signal.CLOSE:
            if position.side is not None and self._can_close_position(symbol):
                self._close_position(symbol, "Mean reversion to VWAP")
            return

        # Buy signal (oversold — Z below -entry AND RSI < oversold)
        if signal == Signal.BUY:
            if position.side == OrderSide.SELL and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to long")

            if position.side is None:
                self._open_position(symbol, OrderSide.BUY, price, current_bar)

        # Sell signal (overbought — Z above entry AND RSI > overbought)
        elif signal == Signal.SELL:
            if position.side == OrderSide.BUY and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to short")

            if position.side is None:
                self._open_position(symbol, OrderSide.SELL, price, current_bar)

    def _open_position(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        current_bar: int,
    ) -> None:
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
        position.entry_bar = current_bar

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

        current_bar = self._bar_count.get(symbol, 0)
        self._cooldown_until[symbol] = current_bar + self._filter.cooldown_bars

        self._daily_stats.trade_count += 1

    def _close_all_positions(self, reason: str) -> None:
        """Close all open positions."""
        for symbol in self._symbols:
            self._close_position(symbol, reason, force=True)

    # Order callbacks

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


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
# =============================================================================
# Mesa #0 = best Sharpe (default)
# Mesa #1 = second best, etc.
#
# Generate configs: uv run python -m strategy.backtest -S vwap -X bitget --heatmap
# List configs:     python -m strategy.bitget.vwap.configs
# Override:         python -m strategy.bitget.vwap.strategy --mesa 1
# =============================================================================

import argparse as _argparse  # noqa: E402

_parser = _argparse.ArgumentParser(add_help=False)
_parser.add_argument("--mesa", type=int, default=0, help="Mesa index (0=best)")
_args, _ = _parser.parse_known_args()

# Load configuration from Mesa configs (heatmap_results.json)
try:
    selected = get_config(_args.mesa)
    strategy_config, filter_config = selected.get_configs()
except (FileNotFoundError, ValueError):
    print("No usable Mesa configs found. Using optimized VWAP config from heatmap scan.")
    from strategy.backtest.config import StrategyConfig as _SC

    # Best parameters from heatmap scan (2026-02-18, 6m, Sharpe=1.008):
    #   zscore_entry=3.79, std_window=193
    # Compared to defaults: zscore_entry=2.0, std_window=200
    strategy_config = VWAPConfig(
        std_window=193,
        zscore_entry=3.79,
    )
    filter_config = VWAPTradeFilterConfig()
    selected = _SC(
        name="Heatmap_Optimized",
        description="Best params from heatmap scan (Sharpe=1.008)",
        strategy_config=strategy_config,
        filter_config=filter_config,
    )

# Clear log file on restart
LOG_DIR = Path(__file__).parent
VWAP_LOG_FILE = LOG_DIR / "vwap.log"
if VWAP_LOG_FILE.exists():
    VWAP_LOG_FILE.write_text("")

# Create strategy instance
strategy = VWAPMeanReversionStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"vwap_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="vwap.log",
    ),
    basic_config={
        ExchangeType.BITGET: BasicConfig(
            api_key=API_KEY,
            secret=SECRET,
            passphrase=PASSPHRASE,
            testnet=True,
        )
    },
    public_conn_config={
        ExchangeType.BITGET: [
            PublicConnectorConfig(
                account_type=BitgetAccountType.UTA_DEMO,
                enable_rate_limit=True,
            )
        ]
    },
    private_conn_config={
        ExchangeType.BITGET: [
            PrivateConnectorConfig(
                account_type=BitgetAccountType.UTA_DEMO,
                enable_rate_limit=True,
                leverage=5,
            )
        ]
    },
)

engine = Engine(config)

if __name__ == "__main__":
    print(f"Log file: {LOG_FILE}")
    try:
        engine.start()
    finally:
        engine.dispose()
        if hasattr(sys.stdout, "close"):
            sys.stdout.close()
