"""
Funding Rate Arbitrage Trading Strategy for Bitget.

This strategy:
- Monitors BTC perpetual contract funding rates
- Opens short positions before 8h funding settlements when rates are positive
- Collects funding payments as profit
- Closes positions after settlement to minimize directional risk
- Uses SMA trend filter to avoid fighting strong uptrends
- 5x leverage with 3% hard stop-loss

Usage:
    uv run python -m strategy.bitget.funding_rate.strategy

Log output:
    strategy/bitget/funding_rate/strategy_output.log
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
from nexustrader.schema import FundingRate, Kline, Order  # noqa: E402
from nexustrader.strategy import Strategy  # noqa: E402

from strategy.bitget.funding_rate.configs import (  # noqa: E402
    FundingRateFilterConfig,
    get_config,
)
from strategy.strategies.funding_rate.core import FundingRateConfig  # noqa: E402
from strategy.bitget.funding_rate.indicator import (  # noqa: E402
    FundingRateIndicator,
    Signal,
)
from strategy.bitget.common.performance import PerformanceTracker  # noqa: E402


# API credentials from settings
API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE

# Funding settlement hours (UTC)
FUNDING_SETTLEMENT_HOURS = (0, 8, 16)


def _hours_until_next_settlement_utc(now_utc: datetime) -> float:
    """Calculate hours until next 8h funding settlement."""
    hour = now_utc.hour + now_utc.minute / 60.0
    for settle_h in FUNDING_SETTLEMENT_HOURS:
        if settle_h > hour:
            return settle_h - hour
    return 24.0 - hour


def _hours_since_last_settlement_utc(now_utc: datetime) -> float:
    """Calculate hours since most recent 8h funding settlement."""
    hour = now_utc.hour + now_utc.minute / 60.0
    for settle_h in reversed(FUNDING_SETTLEMENT_HOURS):
        if settle_h <= hour:
            return hour - settle_h
    return hour + 8.0


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
    funding_collected: float = 0.0


class FundingRateArbitrageStrategy(Strategy):
    """
    Funding Rate Arbitrage quantitative trading strategy.

    Collects funding payments by shorting perpetual contracts when
    funding rate is positive:
    - Enter short before 8h settlement → collect positive funding
    - Exit after settlement → minimize directional risk
    - SMA trend filter to avoid fighting strong uptrends
    - 3% hard stop-loss for capital protection
    """

    # Seconds to wait after warmup completes before allowing trades.
    _WARMUP_SETTLE_S: float = 5.0

    # Maximum allowed age (in seconds) for a kline to be considered "live".
    _MAX_KLINE_AGE_S: float = 120.0  # 2 minutes for 1h candles

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[FundingRateConfig] = None,
        filter_config: Optional[FundingRateFilterConfig] = None,
    ):
        super().__init__()

        self._config = config or FundingRateConfig()
        self._filter = filter_config or FundingRateFilterConfig()

        self._symbols = symbols or self._config.symbols

        # Indicators per symbol
        self._indicators: Dict[str, FundingRateIndicator] = {}

        # Position tracking per symbol
        self._positions: Dict[str, PositionState] = {}

        # Daily statistics
        self._daily_stats = DailyStats()

        # Bar counter per symbol
        self._bar_count: Dict[str, int] = {}

        # Cooldown tracking
        self._cooldown_until: Dict[str, int] = {}

        # Performance tracker (initialized in on_start)
        self._performance: Optional[PerformanceTracker] = None

        # Config info for performance tracking
        self._mesa_index: int = 0
        self._config_name: str = ""

        # Stats logging interval (every N bars)
        self._stats_log_interval: int = 4  # Every 4 hours (4 * 1h)
        self._last_stats_bar: int = 0

        # Live-mode guards
        self._warmup_done_at: Dict[str, float] = {}
        self._live_trading_ready: Dict[str, bool] = {}

        # Stale-data burst detection
        self._stale_burst_count: Dict[str, int] = {}
        self._burst_settle_until: Dict[str, float] = {}

    def set_config_info(self, mesa_index: int, name: str) -> None:
        """Set config info for performance tracking."""
        self._mesa_index = mesa_index
        self._config_name = name

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting Funding Rate Arbitrage Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  min_funding_rate: {self._config.min_funding_rate * 100:.4f}%")
        self.log.info(f"  max_funding_rate: {self._config.max_funding_rate * 100:.4f}%")
        self.log.info(f"  funding_lookback: {self._config.funding_lookback}")
        self.log.info(f"  price_sma_period: {self._config.price_sma_period}")
        self.log.info(f"  max_adverse_move: {self._config.max_adverse_move_pct * 100:.1f}%")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100:.0f}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100:.1f}%")
        self.log.info(f"  hours_before_funding: {self._config.hours_before_funding}")
        self.log.info(f"  hours_after_funding: {self._config.hours_after_funding}")
        self.log.info("Filter Config:")
        self.log.info(f"  min_holding_bars: {self._filter.min_holding_bars}")
        self.log.info(f"  cooldown_bars: {self._filter.cooldown_bars}")
        self.log.info("=" * 60)

        # Initialize daily stats
        self._reset_daily_stats()

        # Performance tracker will be initialized when we get first valid balance
        self._performance_initialized = False
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = FundingRateIndicator(
                config=self._config,
                kline_interval=KlineInterval.HOUR_1,
            )
            self._indicators[symbol] = indicator

            self._positions[symbol] = PositionState(symbol=symbol)
            self._bar_count[symbol] = 0
            self._cooldown_until[symbol] = 0

            # Subscribe to klines for SMA and price tracking
            self.subscribe_kline(symbol, KlineInterval.HOUR_1)
            self.subscribe_bookl1(symbol)

            # Subscribe to funding rate updates
            self.subscribe_funding_rate(symbols=symbol)

            self.register_indicator(
                symbols=symbol,
                indicator=indicator,
                data_type=DataType.KLINE,
            )

            self.log.info(f"Initialized tracking for {symbol}")

    def on_funding_rate(self, funding_rate: FundingRate) -> None:
        """Handle real-time funding rate updates from exchange."""
        symbol = funding_rate.symbol
        rate = funding_rate.rate

        indicator = self._indicators.get(symbol)
        if indicator:
            indicator.set_funding_rate(rate)
            self.log.info(
                f"{symbol} | Funding rate update: {rate * 100:.6f}% "
                f"(avg: {indicator.avg_funding_rate * 100:.6f}%)"
            )

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
        """Check if a kline is stale."""
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
            if current_bar % 5 == 0:
                self.log.info(f"{symbol} | Warming up... bar {current_bar}")
            return

        # Record warmup completion
        if symbol not in self._warmup_done_at:
            self._warmup_done_at[symbol] = time.monotonic()
            self.log.info(
                f"{symbol} | Warmup complete at bar {current_bar}, settling..."
            )

        # Guard: skip trading until settle period
        if not self._check_live_ready(symbol):
            return

        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

        # ---- Stale-data guard ----
        is_stale = self._is_kline_stale(kline)

        if is_stale:
            prev_count = self._stale_burst_count.get(symbol, 0)
            self._stale_burst_count[symbol] = prev_count + 1
            if self._stale_burst_count[symbol] == 1:
                self.log.warning(
                    f"{symbol} | STALE DATA detected (kline age > {self._MAX_KLINE_AGE_S}s), "
                    f"skipping trades until live data resumes"
                )
            return

        # Post-burst settle
        if self._stale_burst_count.get(symbol, 0) > 0:
            burst_size = self._stale_burst_count[symbol]
            self.log.info(
                f"{symbol} | Stale burst ended ({burst_size} bars skipped), "
                f"settling for {self._WARMUP_SETTLE_S}s before trading"
            )
            self._stale_burst_count[symbol] = 0
            self._burst_settle_until[symbol] = time.monotonic() + self._WARMUP_SETTLE_S

        settle_deadline = self._burst_settle_until.get(symbol, 0.0)
        if settle_deadline > 0.0 and time.monotonic() < settle_deadline:
            return

        # Calculate timing
        now_utc = datetime.now(timezone.utc)
        hours_to_next = _hours_until_next_settlement_utc(now_utc)
        hours_since_last = _hours_since_last_settlement_utc(now_utc)

        # Get signal from indicator
        signal = indicator.get_signal(hours_to_next, hours_since_last)

        position = self._positions.get(symbol)
        bars_held = (
            current_bar - position.entry_bar if position and position.side else 0
        )

        pos_str = (
            f"SHORT@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )

        self.log.info(
            f"{symbol} | Bar={current_bar} | SMA={indicator.sma:.1f} | "
            f"FR={indicator.avg_funding_rate * 100:.6f}% | Signal={signal.value} | "
            f"Pos={pos_str} | Hold={bars_held} | "
            f"NextSettlement={hours_to_next:.1f}h | SinceSettlement={hours_since_last:.1f}h"
        )

        # Periodic performance stats
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
                    f"MaxDD: {stats.max_drawdown_pct:.2f}% | "
                    f"Funding: ${self._daily_stats.funding_collected:.2f}"
                )

        # Check circuit breaker
        if self._check_circuit_breaker():
            self._close_all_positions("Circuit breaker active")
            return

        if not position:
            return

        # Check stop loss (always, even during cooldown)
        if position.side is not None:
            if indicator.should_stop_loss(position.entry_price, price):
                self.log.warning(f"STOP LOSS triggered for {symbol}")
                self._close_position(symbol, "Stop loss", force=True)
                return

        # Skip if in cooldown
        if self._is_in_cooldown(symbol):
            return

        # Process signal
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

        # Close signal (after settlement)
        if signal == Signal.CLOSE:
            if position.side is not None and self._can_close_position(symbol):
                self._close_position(symbol, "Post-settlement close")
            return

        # Sell signal (open short before settlement)
        if signal == Signal.SELL:
            if position.side is None:
                self._open_position(symbol, price, current_bar)

    def _open_position(
        self,
        symbol: str,
        price: float,
        current_bar: int,
    ) -> None:
        """Open a short position to collect funding."""
        amount = self._calculate_position_size(symbol, price)

        if amount <= 0:
            self.log.warning("Cannot open SHORT position: amount too small")
            return

        self.log.info(
            f">>> OPENING SHORT position: {symbol} @ {price:.2f}, size={amount} "
            f"(collecting funding rate)"
        )

        self.create_order(
            symbol=symbol,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=amount,
        )

        position = self._positions[symbol]
        position.side = OrderSide.SELL
        position.entry_price = price
        position.amount = amount
        position.entry_time = datetime.now(timezone.utc)
        position.entry_bar = current_bar

        if self._performance:
            self._performance.open_position(symbol, "short", price, float(amount))

    def _close_position(self, symbol: str, reason: str, force: bool = False) -> None:
        """Close existing short position."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

        if not force and not self._can_close_position(symbol):
            return

        self.log.info(f"<<< CLOSING position: {symbol}, reason={reason}")

        exit_price = position.entry_price
        bookl1 = self.cache.bookl1(symbol)
        if bookl1:
            exit_price = float(bookl1.ask)  # Buy to close short

        if self._performance:
            trade = self._performance.close_position(exit_price, reason)
            if trade:
                self.log.info(
                    f"Trade recorded: P&L = {trade.pnl:+.2f} USDT ({trade.pnl_pct:+.2f}%)"
                )
                current_balance = self._get_account_balance()
                self._performance.update_balance(current_balance)

        # Buy to close short
        self.create_order(
            symbol=symbol,
            side=OrderSide.BUY,
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
# CONFIGURATION SELECTION
# =============================================================================

import argparse as _argparse  # noqa: E402

_parser = _argparse.ArgumentParser(add_help=False)
_parser.add_argument("--mesa", type=int, default=0, help="Mesa index (0=best)")
_args, _ = _parser.parse_known_args()

# Load configuration from Mesa configs
try:
    selected = get_config(_args.mesa)
    strategy_config, filter_config = selected.get_configs()
except (FileNotFoundError, ValueError):
    print("No usable Mesa configs found. Using default Funding Rate config.")
    from strategy.backtest.config import StrategyConfig as _SC

    strategy_config = FundingRateConfig(
        min_funding_rate=0.0005,
        max_funding_rate=0.01,
        funding_lookback=24,
        price_sma_period=50,
        max_adverse_move_pct=0.02,
        position_size_pct=0.30,
        stop_loss_pct=0.03,
        daily_loss_limit=0.02,
        hours_before_funding=2,
        hours_after_funding=1,
    )
    filter_config = FundingRateFilterConfig()
    selected = _SC(
        name="Default_FundingRate",
        description="Default funding rate arbitrage config",
        strategy_config=strategy_config,
        filter_config=filter_config,
    )

# Clear log file on restart
LOG_DIR = Path(__file__).parent
FR_LOG_FILE = LOG_DIR / "funding_rate.log"
if FR_LOG_FILE.exists():
    FR_LOG_FILE.write_text("")

# Create strategy instance
strategy = FundingRateArbitrageStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"funding_rate_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="funding_rate.log",
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
