"""
Multi-Timeframe Momentum Trading Strategy for Bitget.

This strategy uses:
- ROC (Rate of Change) for momentum detection
- Triple EMA (fast / slow / trend) for multi-timeframe trend confirmation
- ATR trailing stop for volatility-adaptive exits
- Volume SMA for volume confirmation
- 1-hour candle timeframe

Usage:
    uv run python -m strategy.bitget.momentum.strategy

Log output:
    strategy/bitget/momentum/strategy_output.log
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

from strategy.bitget.momentum.configs import (  # noqa: E402
    MomentumTradeFilterConfig,
    get_config,
)
from strategy.strategies.momentum.core import MomentumConfig  # noqa: E402
from strategy.bitget.momentum.indicator import (  # noqa: E402
    MomentumIndicator,
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
    trailing_stop: float = 0.0  # ATR trailing stop level


@dataclass
class DailyStats:
    """Track daily trading statistics."""

    date: str = ""
    starting_balance: float = 0.0
    realized_pnl: float = 0.0
    trade_count: int = 0
    is_circuit_breaker_active: bool = False


class MomentumStrategy(Strategy):
    """
    Multi-Timeframe Momentum quantitative trading strategy.

    Uses ROC + EMA (fast/slow/trend) + ATR + Volume for momentum-following:
    - ROC > threshold AND EMA_f > EMA_s AND price > EMA_trend AND vol_ok -> BUY
    - ROC < -threshold AND EMA_f < EMA_s AND price < EMA_trend AND vol_ok -> SELL
    - ROC reversal / EMA crossover reversal / ATR trailing stop -> EXIT
    """

    # Seconds to wait after warmup completes before allowing trades.
    _WARMUP_SETTLE_S: float = 5.0

    # Maximum allowed age (in seconds) for a kline to be considered "live".
    _MAX_KLINE_AGE_S: float = 120.0  # 2 min (1h candle has wider tolerance)

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[MomentumConfig] = None,
        filter_config: Optional[MomentumTradeFilterConfig] = None,
    ):
        super().__init__()

        self._config = config or MomentumConfig()
        self._filter = filter_config or MomentumTradeFilterConfig()

        self._symbols = symbols or self._config.symbols

        # Indicators per symbol
        self._indicators: Dict[str, MomentumIndicator] = {}

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
        self._stats_log_interval: int = 6  # Every 6 hours (6 * 1h)
        self._last_stats_bar: int = 0

        # Live-mode guard
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
        self.log.info("Starting Multi-Timeframe Momentum Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  roc_period: {self._config.roc_period}")
        self.log.info(f"  roc_threshold: {self._config.roc_threshold}")
        self.log.info(f"  ema_fast: {self._config.ema_fast}")
        self.log.info(f"  ema_slow: {self._config.ema_slow}")
        self.log.info(f"  ema_trend: {self._config.ema_trend}")
        self.log.info(f"  atr_period: {self._config.atr_period}")
        self.log.info(f"  atr_multiplier: {self._config.atr_multiplier}")
        self.log.info(f"  volume_sma_period: {self._config.volume_sma_period}")
        self.log.info(f"  volume_threshold: {self._config.volume_threshold}")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100}%")
        self.log.info("Filter Config:")
        self.log.info(
            f"  min_holding_bars: {self._filter.min_holding_bars} ({self._filter.min_holding_bars * 60} min)"
        )
        self.log.info(
            f"  cooldown_bars: {self._filter.cooldown_bars} ({self._filter.cooldown_bars * 60} min)"
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
            indicator = MomentumIndicator(
                config=self._config,
                kline_interval=KlineInterval.HOUR_1,
            )
            self._indicators[symbol] = indicator

            self._positions[symbol] = PositionState(symbol=symbol)
            self._bar_count[symbol] = 0
            self._cooldown_until[symbol] = 0
            self._signal_history[symbol] = []

            self.subscribe_kline(symbol, KlineInterval.HOUR_1)
            self.subscribe_bookl1(symbol)

            self.register_indicator(
                symbols=symbol,
                indicator=indicator,
                data_type=DataType.KLINE,
            )

            self.log.info(f"Initialized tracking for {symbol}")

            # Manual warmup: fetch confirmed historical klines via REST API
            self._manual_warmup(symbol, indicator)

        # Sync existing positions from exchange on startup
        self._sync_positions_on_start()

    def _manual_warmup(self, symbol: str, indicator) -> None:
        """Manually fetch historical confirmed klines via ccxt and feed to indicator.

        Bitget's NexusTrader connector doesn't implement request_klines,
        so we use ccxt directly to fetch public OHLCV data.
        """
        import ccxt
        from nexustrader.schema import Kline as KlineSchema

        try:
            warmup_bars = indicator._real_warmup_period
            exchange = ccxt.bitget()

            # Convert symbol: "BTCUSDT-PERP.BITGET" -> "BTC/USDT:USDT"
            raw = symbol.split(".")[0].replace("-PERP", "")  # "BTCUSDT"
            base = raw[:-4]  # "BTC"
            quote = raw[-4:]  # "USDT"
            ccxt_symbol = f"{base}/{quote}:{quote}"

            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, "1h", limit=warmup_bars)

            for row in ohlcv:
                ts, o, h, l, c, v = row
                kline = KlineSchema(
                    exchange=ExchangeType.BITGET,
                    symbol=symbol,
                    interval=KlineInterval.HOUR_1,
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=float(v),
                    start=int(ts),
                    timestamp=int(ts) + 3600000,
                    confirm=True,
                )
                indicator._process_kline_data(kline)
                indicator._confirmed_bar_count += 1

            self.log.info(
                f"{symbol} | Manual warmup via ccxt: {len(ohlcv)} bars "
                f"(need {warmup_bars}, warmed_up={indicator.is_warmed_up})"
            )
        except Exception as e:
            self.log.warning(
                f"{symbol} | Manual warmup failed: {e}. "
                f"Will warm up from live confirmed klines (~{warmup_bars}h)"
            )

    def _sync_positions_on_start(self) -> None:
        """Sync existing exchange positions into strategy state on startup.

        If the strategy restarts while a position is open on the exchange,
        this ensures we track it properly (with stop-loss and trailing stop)
        rather than ignoring it as an orphan.
        """
        for symbol in self._symbols:
            try:
                exchange_pos = self.cache.get_position(symbol)
                # Handle Option/Some wrapper if present
                if exchange_pos is not None and hasattr(exchange_pos, 'value_or'):
                    exchange_pos = exchange_pos.value_or(None)

                if exchange_pos is None:
                    self.log.info(f"{symbol} | No existing position on exchange")
                    continue

                signed_amt = exchange_pos.signed_amount
                if signed_amt == 0 or abs(signed_amt) == 0:
                    self.log.info(f"{symbol} | No existing position on exchange")
                    continue

                # Map exchange position to our internal state
                pos_state = self._positions[symbol]
                entry_price = float(exchange_pos.entry_price)

                if signed_amt > 0:
                    pos_state.side = OrderSide.BUY
                elif signed_amt < 0:
                    pos_state.side = OrderSide.SELL
                else:
                    continue

                pos_state.entry_price = entry_price
                pos_state.amount = abs(signed_amt)
                pos_state.entry_bar = 0  # Unknown, use 0
                pos_state.entry_time = datetime.now(timezone.utc)

                # Initialize trailing stop conservatively (will tighten as bars flow in)
                # We don't have ATR yet (warmup), so use stop_loss_pct as fallback
                if pos_state.side == OrderSide.BUY:
                    pos_state.trailing_stop = entry_price * (1 - self._config.stop_loss_pct)
                else:
                    pos_state.trailing_stop = entry_price * (1 + self._config.stop_loss_pct)

                self.log.warning(
                    f"{symbol} | EXISTING POSITION DETECTED: "
                    f"{pos_state.side.value} {pos_state.amount} @ {entry_price:.2f}, "
                    f"trailing_stop={pos_state.trailing_stop:.2f}"
                )
                self.log.warning(
                    f"{symbol} | Position will be managed by strategy. "
                    f"ATR trailing stop will update after warmup."
                )
            except Exception as e:
                self.log.warning(
                    f"{symbol} | Failed to sync position from exchange: {e}. "
                    f"Starting with FLAT state."
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

    def _check_realtime_stops(self, symbol: str, price: float) -> None:
        """Check stop-loss and trailing stop on unconfirmed (real-time) klines.

        This ensures we react to price movements between confirmed bars
        without polluting indicator state with intra-bar updates.
        """
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

        indicator = self._indicators.get(symbol)
        if not indicator:
            return

        is_long = position.side == OrderSide.BUY

        # Hard stop loss
        if indicator.should_stop_loss(position.entry_price, price, is_long):
            self.log.warning(f"HARD STOP LOSS triggered (real-time) for {symbol} @ {price:.1f}")
            self._close_position(symbol, "Hard stop loss (real-time)", force=True)
            return

        # ATR trailing stop check (don't update the stop, just check)
        if is_long and position.trailing_stop > 0 and price < position.trailing_stop:
            self.log.warning(
                f"ATR TRAILING STOP triggered (real-time) for {symbol} "
                f"@ {price:.1f} < stop {position.trailing_stop:.1f}"
            )
            self._close_position(symbol, "ATR trailing stop (real-time)", force=True)
            return
        elif not is_long and position.trailing_stop > 0 and price > position.trailing_stop:
            self.log.warning(
                f"ATR TRAILING STOP triggered (real-time) for {symbol} "
                f"@ {price:.1f} > stop {position.trailing_stop:.1f}"
            )
            self._close_position(symbol, "ATR trailing stop (real-time)", force=True)
            return

    def _is_kline_stale(self, kline) -> bool:
        """Check if a kline is stale (too old compared to wall-clock time)."""
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
        """Process new kline data.

        Bar confirmation is detected by timestamp change (kline.start),
        NOT by kline.confirm (which is hardcoded False in Bitget connector).
        When a new bar starts, the previous bar is considered confirmed.
        """
        symbol = kline.symbol
        price = float(kline.close)

        # Detect new bar by comparing start timestamp
        bar_start = int(kline.start)
        if not hasattr(self, '_last_bar_start'):
            self._last_bar_start = {}
        prev_bar_start = self._last_bar_start.get(symbol, 0)
        is_new_bar = (bar_start != prev_bar_start and prev_bar_start != 0)
        self._last_bar_start[symbol] = bar_start

        # Always let indicator handle the kline (it tracks bar changes internally)
        indicator = self._indicators.get(symbol)
        if not indicator:
            return
        indicator.handle_kline(kline)

        if not is_new_bar:
            # Same bar: only check stop-loss and log periodic status
            self._check_realtime_stops(symbol, price)
            now = time.monotonic()
            if not hasattr(self, '_last_tick_log'):
                self._last_tick_log = {}
            last_tick_log = self._last_tick_log.get(symbol, 0.0)
            if now - last_tick_log >= 30:
                self._last_tick_log[symbol] = now
                position = self._positions.get(symbol)
                pos_str = (
                    f"{position.side.value}@{position.entry_price:.0f}"
                    if position and position.side
                    else "FLAT"
                )
                adx_str = f"{indicator.adx:.1f}" if indicator and indicator.adx is not None else "N/A"
                regime_str = "TREND" if indicator and indicator.is_trending else "RANGE"
                self.log.info(
                    f"{symbol} | [tick] Price={price:.1f} | ADX={adx_str} ({regime_str}) | "
                    f"Pos={pos_str}"
                )
            return

        # === New bar detected → previous bar is confirmed ===
        self._bar_count[symbol] = self._bar_count.get(symbol, 0) + 1
        current_bar = self._bar_count[symbol]

        self._reset_daily_stats()
        self._init_performance_tracker()

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

        # Stale-data guard
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

        if self._stale_burst_count.get(symbol, 0) > 0:
            burst_size = self._stale_burst_count[symbol]
            self.log.info(
                f"{symbol} | Stale burst ended ({burst_size} bars skipped), "
                f"settling for {self._WARMUP_SETTLE_S}s before trading"
            )
            self._stale_burst_count[symbol] = 0
            self._burst_settle_until[symbol] = time.monotonic() + self._WARMUP_SETTLE_S
            self._signal_history[symbol] = []

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

        roc_str = f"{indicator.roc * 100:.2f}%"
        atr_str = f"{indicator.atr:.1f}" if indicator.atr else "N/A"
        adx_str = f"{indicator.adx:.1f}" if indicator.adx is not None else "N/A"
        regime_str = "TREND" if indicator.is_trending else "RANGE"

        self.log.info(
            f"{symbol} | Bar={current_bar} | ROC={roc_str} | ATR={atr_str} | "
            f"ADX={adx_str} ({regime_str}) | "
            f"EMA_f={'N/A' if indicator.ema_fast is None else f'{indicator.ema_fast:.1f}'} | "
            f"EMA_s={'N/A' if indicator.ema_slow is None else f'{indicator.ema_slow:.1f}'} | "
            f"Signal={signal.value} | Pos={pos_str} | "
            f"Hold={bars_held} | CD={cooldown_remaining}"
        )

        # Regime filter: close positions when market switches to ranging
        if not indicator.is_trending and position and position.side is not None:
            if bars_held >= self._filter.min_holding_bars:
                self.log.warning(
                    f"{symbol} | REGIME CHANGE: Ranging market detected (ADX={adx_str}), "
                    f"closing {position.side.value} position"
                )
                self._close_position(symbol, "Regime filter: ranging market")
                return

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

        # Check stop loss / trailing stop / momentum reversal (always allowed)
        if position.side is not None:
            is_long = position.side == OrderSide.BUY

            # Hard stop loss
            if indicator.should_stop_loss(position.entry_price, price, is_long):
                self.log.warning(f"HARD STOP LOSS triggered for {symbol}")
                self._close_position(symbol, "Hard stop loss", force=True)
                return

            # ATR trailing stop
            if is_long and indicator.atr is not None:
                new_stop = price - indicator.atr * self._config.atr_multiplier
                position.trailing_stop = max(position.trailing_stop, new_stop)
                if price < position.trailing_stop:
                    self.log.warning(f"ATR TRAILING STOP triggered for {symbol} (stop={position.trailing_stop:.1f})")
                    self._close_position(symbol, "ATR trailing stop", force=True)
                    return
            elif not is_long and indicator.atr is not None:
                new_stop = price + indicator.atr * self._config.atr_multiplier
                if position.trailing_stop == 0.0:
                    position.trailing_stop = new_stop
                else:
                    position.trailing_stop = min(position.trailing_stop, new_stop)
                if price > position.trailing_stop:
                    self.log.warning(f"ATR TRAILING STOP triggered for {symbol} (stop={position.trailing_stop:.1f})")
                    self._close_position(symbol, "ATR trailing stop", force=True)
                    return

            # Momentum/trend reversal exit
            if is_long and indicator.should_exit_long():
                if self._can_close_position(symbol):
                    self._close_position(symbol, "Momentum reversal (long)")
                    return
            elif not is_long and indicator.should_exit_short():
                if self._can_close_position(symbol):
                    self._close_position(symbol, "Momentum reversal (short)")
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

        # Buy signal
        if signal == Signal.BUY:
            if position.side == OrderSide.SELL and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to long")
                return  # Enforce cooldown — no same-bar re-entry (matches backtest)

            if position.side is None:
                self._open_position(symbol, OrderSide.BUY, price, current_bar)

        # Sell signal
        elif signal == Signal.SELL:
            if position.side == OrderSide.BUY and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to short")
                return  # Enforce cooldown — no same-bar re-entry (matches backtest)

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

        indicator = self._indicators.get(symbol)
        position = self._positions[symbol]
        position.side = side
        position.entry_price = price
        position.amount = amount
        position.entry_time = datetime.now(timezone.utc)
        position.entry_bar = current_bar

        # Initialize trailing stop
        if indicator and indicator.atr is not None:
            if side == OrderSide.BUY:
                position.trailing_stop = price - indicator.atr * self._config.atr_multiplier
            else:
                position.trailing_stop = price + indicator.atr * self._config.atr_multiplier
        else:
            position.trailing_stop = 0.0

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
        position.trailing_stop = 0.0

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
    print("No usable Mesa configs found. Using default Momentum config.")
    from strategy.backtest.config import StrategyConfig as _SC

    strategy_config = MomentumConfig()
    filter_config = MomentumTradeFilterConfig()
    selected = _SC(
        name="Default",
        description="Default Momentum config",
        strategy_config=strategy_config,
        filter_config=filter_config,
    )

# Clear log file on restart
LOG_DIR = Path(__file__).parent
MOMENTUM_LOG_FILE = LOG_DIR / "momentum.log"
if MOMENTUM_LOG_FILE.exists():
    MOMENTUM_LOG_FILE.write_text("")

# Create strategy instance
strategy = MomentumStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"momentum_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="momentum.log",
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
