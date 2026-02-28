"""
Multi-Timeframe Momentum Trading Strategy.

This strategy uses:
- ROC (Rate of Change) for momentum detection
- Triple EMA (fast / slow / trend) for multi-timeframe trend confirmation
- ATR trailing stop for volatility-adaptive exits
- Volume SMA for volume confirmation
- 1-hour candle timeframe

Usage:
    uv run python -m strategy.strategies.momentum.live

Log output:
    strategy/strategies/momentum/strategy_output.log
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from strategy.strategies._base.base_strategy import LogTee

# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "strategy_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.constants import (  # noqa: E402
    ExchangeType,
    KlineInterval,
    OrderSide,
)
from nexustrader.schema import Kline  # noqa: E402

from strategy.strategies._base.base_strategy import (  # noqa: E402
    BaseQuantStrategy,
)
from strategy.strategies.momentum.configs import (  # noqa: E402
    MomentumTradeFilterConfig,
    get_config,
)
from strategy.strategies.momentum.core import MomentumConfig  # noqa: E402
from strategy.strategies.momentum.indicator import (  # noqa: E402
    MomentumIndicator,
)


class MomentumStrategy(BaseQuantStrategy):
    """
    Multi-Timeframe Momentum quantitative trading strategy.

    Uses ROC + EMA (fast/slow/trend) + ATR + Volume for momentum-following:
    - ROC > threshold AND EMA_f > EMA_s AND price > EMA_trend AND vol_ok -> BUY
    - ROC < -threshold AND EMA_f < EMA_s AND price < EMA_trend AND vol_ok -> SELL
    - ROC reversal / EMA crossover reversal / ATR trailing stop -> EXIT

    Overrides on_kline entirely for bar detection + tick-level stop checks.
    """

    _ENABLE_STALE_GUARD: bool = True
    _MAX_KLINE_AGE_S: float = 120.0  # 2 min (1h candle has wider tolerance)

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[MomentumConfig] = None,
        filter_config: Optional[MomentumTradeFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or MomentumConfig()
        filter_config = filter_config or MomentumTradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )
        self._stats_log_interval = 6  # Every 6 hours (6 * 1h)

        # Momentum-specific state
        self._trailing_stops: Dict[str, float] = {}
        self._last_bar_start: Dict[str, int] = {}
        self._last_tick_log: Dict[str, float] = {}

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

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = MomentumIndicator(
                config=self._config,
                kline_interval=KlineInterval.HOUR_1,
            )
            self._register_symbol(symbol, indicator, KlineInterval.HOUR_1)
            self.log.info(f"Initialized tracking for {symbol}")

            # Manual warmup: fetch confirmed historical klines via REST API
            self._manual_warmup(symbol, indicator)

        # Sync existing positions from exchange on startup
        self._sync_positions_on_start()

    def _manual_warmup(self, symbol: str, indicator) -> None:
        """Manually fetch historical confirmed klines via ccxt and feed to indicator."""
        import ccxt
        from nexustrader.schema import Kline as KlineSchema

        try:
            warmup_bars = indicator._real_warmup_period
            exchange_id = self._symbols[0].split(".")[-1].lower()
            exchange = getattr(ccxt, exchange_id)()

            # Convert symbol: "BTCUSDT-PERP.BITGET" -> "BTC/USDT:USDT"
            raw = symbol.split(".")[0].replace("-PERP", "")  # "BTCUSDT"
            base = raw[:-4]  # "BTC"
            quote = raw[-4:]  # "USDT"
            ccxt_symbol = f"{base}/{quote}:{quote}"

            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, "1h", limit=warmup_bars)

            for row in ohlcv:
                ts, o, h, low, c, v = row
                kline = KlineSchema(
                    exchange=ExchangeType.BITGET,
                    symbol=symbol,
                    interval=KlineInterval.HOUR_1,
                    open=float(o),
                    high=float(h),
                    low=float(low),
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
        """Sync existing exchange positions into strategy state on startup."""
        for symbol in self._symbols:
            try:
                exchange_pos = self.cache.get_position(symbol)
                if exchange_pos is not None and hasattr(exchange_pos, "value_or"):
                    exchange_pos = exchange_pos.value_or(None)

                if exchange_pos is None:
                    self.log.info(f"{symbol} | No existing position on exchange")
                    continue

                signed_amt = exchange_pos.signed_amount
                if signed_amt == 0 or abs(signed_amt) == 0:
                    self.log.info(f"{symbol} | No existing position on exchange")
                    continue

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
                pos_state.entry_bar = 0
                pos_state.entry_time = datetime.now(timezone.utc)

                # Initialize trailing stop conservatively
                if pos_state.side == OrderSide.BUY:
                    self._trailing_stops[symbol] = entry_price * (
                        1 - self._config.stop_loss_pct
                    )
                else:
                    self._trailing_stops[symbol] = entry_price * (
                        1 + self._config.stop_loss_pct
                    )

                self.log.warning(
                    f"{symbol} | EXISTING POSITION DETECTED: "
                    f"{pos_state.side.value} {pos_state.amount} @ {entry_price:.2f}, "
                    f"trailing_stop={self._trailing_stops[symbol]:.2f}"
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

    def _check_realtime_stops(self, symbol: str, price: float) -> None:
        """Check stop-loss and trailing stop on unconfirmed (real-time) klines."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

        indicator = self._indicators.get(symbol)
        if not indicator:
            return

        is_long = position.side == OrderSide.BUY

        # Hard stop loss
        if indicator.should_stop_loss(position.entry_price, price, is_long):
            self.log.warning(
                f"HARD STOP LOSS triggered (real-time) for {symbol} @ {price:.1f}"
            )
            self._close_position(symbol, "Hard stop loss (real-time)", force=True)
            return

        # ATR trailing stop check (don't update the stop, just check)
        trailing_stop = self._trailing_stops.get(symbol, 0.0)
        if is_long and trailing_stop > 0 and price < trailing_stop:
            self.log.warning(
                f"ATR TRAILING STOP triggered (real-time) for {symbol} "
                f"@ {price:.1f} < stop {trailing_stop:.1f}"
            )
            self._close_position(symbol, "ATR trailing stop (real-time)", force=True)
            return
        elif not is_long and trailing_stop > 0 and price > trailing_stop:
            self.log.warning(
                f"ATR TRAILING STOP triggered (real-time) for {symbol} "
                f"@ {price:.1f} > stop {trailing_stop:.1f}"
            )
            self._close_position(symbol, "ATR trailing stop (real-time)", force=True)
            return

    def on_kline(self, kline: Kline) -> None:
        """Override: adds bar detection + tick-level stop checks.

        Bar confirmation is detected by timestamp change (kline.start),
        NOT by kline.confirm (which is hardcoded False in Bitget connector).
        """
        symbol = kline.symbol
        price = float(kline.close)

        # Detect new bar by comparing start timestamp
        bar_start = int(kline.start)
        prev_bar_start = self._last_bar_start.get(symbol, 0)
        is_new_bar = bar_start != prev_bar_start and prev_bar_start != 0
        self._last_bar_start[symbol] = bar_start

        # Always let indicator handle the kline
        indicator = self._indicators.get(symbol)
        if not indicator:
            return
        indicator.handle_kline(kline)

        if not is_new_bar:
            # Same bar: only check stop-loss and log periodic status
            if self._live_trading_ready.get(symbol, False):
                self._check_realtime_stops(symbol, price)
                now = time.monotonic()
                last_tick_log = self._last_tick_log.get(symbol, 0.0)
                if now - last_tick_log >= 30:
                    self._last_tick_log[symbol] = now
                    position = self._positions.get(symbol)
                    pos_str = (
                        f"{position.side.value}@{position.entry_price:.0f}"
                        if position and position.side
                        else "FLAT"
                    )
                    adx_str = (
                        f"{indicator.adx:.1f}"
                        if indicator and indicator.adx is not None
                        else "N/A"
                    )
                    regime_str = (
                        "TREND" if indicator and indicator.is_trending else "RANGE"
                    )
                    self.log.info(
                        f"{symbol} | [tick] Price={price:.1f} | ADX={adx_str} ({regime_str}) | "
                        f"Pos={pos_str}"
                    )
            return

        # === New bar detected -> previous bar is confirmed ===
        self._bar_count[symbol] = self._bar_count.get(symbol, 0) + 1
        current_bar = self._bar_count[symbol]

        self._reset_daily_stats()
        self._init_performance_tracker()

        if not indicator.is_warmed_up:
            if current_bar % 10 == 0:
                self.log.info(f"{symbol} | Warming up... bar {current_bar}")
            return

        # Record warmup completion
        if symbol not in self._warmup_done_at:
            self._warmup_done_at[symbol] = time.monotonic()
            self.log.info(
                f"{symbol} | Warmup complete at bar {current_bar}, settling..."
            )

        if not self._check_live_ready(symbol):
            return

        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

        # Stale data guard
        if self._check_stale_data(symbol, kline):
            return

        signal = self._get_signal(symbol, indicator)
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

        # Periodic stats
        self._log_periodic_stats(symbol)

        # Circuit breaker
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
            trailing_stop = self._trailing_stops.get(symbol, 0.0)
            if is_long and indicator.atr is not None:
                new_stop = price - indicator.atr * self._config.atr_multiplier
                trailing_stop = max(trailing_stop, new_stop)
                self._trailing_stops[symbol] = trailing_stop
                if price < trailing_stop:
                    self.log.warning(
                        f"ATR TRAILING STOP triggered for {symbol} (stop={trailing_stop:.1f})"
                    )
                    self._close_position(symbol, "ATR trailing stop", force=True)
                    return
            elif not is_long and indicator.atr is not None:
                new_stop = price + indicator.atr * self._config.atr_multiplier
                if trailing_stop == 0.0:
                    trailing_stop = new_stop
                else:
                    trailing_stop = min(trailing_stop, new_stop)
                self._trailing_stops[symbol] = trailing_stop
                if price > trailing_stop:
                    self.log.warning(
                        f"ATR TRAILING STOP triggered for {symbol} (stop={trailing_stop:.1f})"
                    )
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
        self, symbol: str, signal, price: float, current_bar: int
    ) -> None:
        """Override: enforce cooldown on reversal (no same-bar re-entry)."""
        position = self._positions.get(symbol)
        if not position:
            return

        sig = signal.value

        if sig in ("buy", "sell") and not self._is_signal_confirmed(symbol, signal):
            return

        # Buy signal
        if sig == "buy":
            if position.side == OrderSide.SELL and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to long")
                return  # Enforce cooldown -- no same-bar re-entry (matches backtest)

            if position.side is None:
                self._open_position(symbol, OrderSide.BUY, price, current_bar)

        # Sell signal
        elif sig == "sell":
            if position.side == OrderSide.BUY and self._can_close_position(symbol):
                self._close_position(symbol, "Reversing to short")
                return  # Enforce cooldown -- no same-bar re-entry (matches backtest)

            if position.side is None:
                self._open_position(symbol, OrderSide.SELL, price, current_bar)

    def _open_position(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        current_bar: Optional[int] = None,
    ) -> None:
        """Override: add trailing stop initialization."""
        super()._open_position(symbol, side, price, current_bar)

        # Initialize trailing stop
        indicator = self._indicators.get(symbol)
        if indicator and indicator.atr is not None:
            if side == OrderSide.BUY:
                self._trailing_stops[symbol] = (
                    price - indicator.atr * self._config.atr_multiplier
                )
            else:
                self._trailing_stops[symbol] = (
                    price + indicator.atr * self._config.atr_multiplier
                )
        else:
            self._trailing_stops[symbol] = 0.0

    def _close_position(self, symbol: str, reason: str, force: bool = False) -> None:
        """Override: clear trailing stop on close."""
        super()._close_position(symbol, reason, force)
        self._trailing_stops[symbol] = 0.0


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

# --- Runner (Bitget demo) ---
from nexustrader.config import (  # noqa: E402
    BasicConfig,
    Config,
    LogConfig,
    PrivateConnectorConfig,
    PublicConnectorConfig,
)
from nexustrader.constants import settings  # noqa: E402
from nexustrader.engine import Engine  # noqa: E402
from nexustrader.exchange import BitgetAccountType  # noqa: E402

API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE

# Create strategy instance
strategy = MomentumStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
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
