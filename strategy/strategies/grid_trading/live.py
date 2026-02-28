"""
Grid Trading Strategy.

This strategy uses:
- SMA(20) for grid center calculation
- ATR(14) for dynamic grid range (ATR * 3.0)
- 4 grid levels (0-3) recalculated every 24 hours
- Entry when price moves 1+ grid lines
- Profit taking when price reverses 2+ grid lines
- 3% stop loss protection
- 5x leverage

Usage:
    uv run python -m strategy.strategies.grid_trading

Log output:
    strategy/strategies/grid_trading/grid_output.log
"""

import base64
import hashlib
import hmac
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

import requests as _requests

from strategy.strategies._base.base_strategy import LogTee

# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "grid_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.constants import (  # noqa: E402
    DataType,
    ExchangeType,
    KlineInterval,
    OrderSide,
    OrderType,
)
from nexustrader.schema import Kline, Order  # noqa: E402

from strategy.strategies._base.base_strategy import (  # noqa: E402
    BaseQuantStrategy,
    PositionState,
)
from strategy.strategies.grid_trading.indicator import (  # noqa: E402
    GridIndicator,
)


class GridStrategy(BaseQuantStrategy):
    """
    Grid Trading Strategy using SMA+ATR dynamic grids.

    Logic:
    - FLAT + price drops >= entry_lines + current_level <= grid_count//2 -> BUY
    - FLAT + price rises >= entry_lines + current_level >= grid_count//2 -> SELL
    - LONG + price rises >= profit_lines from trough -> CLOSE
    - SHORT + price falls >= profit_lines from peak -> CLOSE
    - Stop loss: 3% hard stop

    Overrides on_kline entirely — grid uses tick-based logic with LIMIT orders,
    reconciliation, burst detection, and out-of-grid protection.
    """

    # Grid configuration
    _GRID_COUNT: int = 3
    _ATR_MULTIPLIER: float = 3.0
    _SMA_PERIOD: int = 20
    _ATR_PERIOD: int = 14
    _RECALC_PERIOD: int = 6
    _ENTRY_LINES: int = 1
    _PROFIT_LINES: int = 2
    _STOP_LOSS_PCT: float = 0.03
    _POSITION_SIZE_PCT: float = 0.20

    # Timing
    _WARMUP_SETTLE_S: float = 5.0
    _MAX_KLINE_AGE_S: float = 120.0
    _ENABLE_STALE_GUARD: bool = True

    # Out-of-grid protection
    _OUT_OF_GRID_MAX_BARS: int = 3

    # Position reconciliation
    _RECONCILE_INTERVAL_S: float = 60.0
    _MAX_POSITION_SIZE: float = 0.5

    def __init__(self, symbols: Optional[List[str]] = None, *, account_type):
        super().__init__()
        self._account_type = account_type
        self._symbols = symbols or ["BTCUSDT-PERP.BITGET"]

        # Per-symbol state (grid doesn't use _init_common since no config/filter)
        self._indicators: Dict[str, GridIndicator] = {}
        self._positions: Dict[str, PositionState] = {}
        self._bar_count: Dict[str, int] = {}
        self._warmup_done_at: Dict[str, float] = {}
        self._live_trading_ready: Dict[str, bool] = {}
        self._stale_burst_count: Dict[str, int] = {}
        self._burst_settle_until: Dict[str, float] = {}

        # Grid-specific state
        self._out_of_grid_bars: Dict[str, int] = {}
        self._last_reconcile_at: float = 0.0
        self._reconcile_mismatch_count: Dict[str, int] = {}

    def _calculate_position_size(self, symbol: str, price: float) -> Decimal:
        """Calculate position size based on account balance."""
        balance = self._get_account_balance()
        position_value = balance * self._POSITION_SIZE_PCT
        amount = position_value / price
        return self.amount_to_precision(symbol, Decimal(str(amount)))

    def _manual_warmup(self, symbol: str, indicator: GridIndicator) -> None:
        """Manually fetch historical klines via ccxt for indicator warmup."""
        import ccxt
        from nexustrader.schema import Kline as KlineSchema

        try:
            warmup_bars = indicator._real_warmup_period
            exchange_id = self._symbols[0].split(".")[-1].lower()
            exchange = getattr(ccxt, exchange_id)()

            raw = symbol.split(".")[0].replace("-PERP", "")
            base = raw[:-4]
            quote = raw[-4:]
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

            if indicator.is_warmed_up and indicator.grid_lines is not None:
                self.log.info(
                    f"{symbol} | Initial grid setup: "
                    f"SMA={indicator.sma:.1f}, ATR={indicator.atr:.1f}, "
                    f"Range=[{indicator.grid_lower:.1f}, {indicator.grid_upper:.1f}], "
                    f"Levels={[f'{level:.1f}' for level in indicator.grid_lines]}"
                )

        except Exception as e:
            self.log.warning(
                f"{symbol} | Manual warmup failed: {e}. "
                f"Will warm up from live klines (~{warmup_bars}h)"
            )

    def _sync_positions_on_start(self) -> None:
        """Sync existing exchange positions into strategy state on startup."""
        for symbol in self._symbols:
            try:
                exchange_pos = self.cache.get_position(symbol)
                if exchange_pos is not None and hasattr(exchange_pos, "value_or"):
                    exchange_pos = exchange_pos.value_or(None)

                if exchange_pos is None or exchange_pos.signed_amount == 0:
                    self.log.info(f"{symbol} | No existing position on exchange")
                    continue

                pos_state = self._positions[symbol]
                signed_amt = exchange_pos.signed_amount
                entry_price = float(exchange_pos.entry_price)

                if signed_amt > 0:
                    pos_state.side = OrderSide.BUY
                elif signed_amt < 0:
                    pos_state.side = OrderSide.SELL
                else:
                    continue

                pos_state.entry_price = entry_price
                pos_state.amount = abs(signed_amt)
                pos_state.entry_time = datetime.now(timezone.utc)
                pos_state.entry_bar = 0

                self.log.warning(
                    f"{symbol} | EXISTING POSITION DETECTED: "
                    f"{pos_state.side.value} {pos_state.amount} @ {entry_price:.2f}"
                )

            except Exception as e:
                self.log.warning(
                    f"{symbol} | Failed to sync position from exchange: {e}. "
                    f"Starting with FLAT state."
                )

    def _should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Check if stop loss should be triggered."""
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct < -self._STOP_LOSS_PCT

    def _get_kline_age_s(self, kline) -> float:
        """Get kline age in seconds."""
        now_ms = int(time.time() * 1000)
        return (now_ms - kline.timestamp) / 1000.0

    def _check_realtime_stops(self, symbol: str, price: float) -> None:
        """Check stop-loss on real-time price movements."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return
        if self._should_stop_loss(
            position.entry_price, price, position.side == OrderSide.BUY
        ):
            self.log.warning(
                f"STOP LOSS triggered (real-time) for {symbol} @ {price:.1f}"
            )
            self._close_grid_position(symbol, "Stop loss (real-time)", price)

    def _reconcile_position(self, symbol: str, price: float) -> None:
        """Periodically reconcile internal position state with exchange."""
        now_mono = time.monotonic()
        if now_mono - self._last_reconcile_at < self._RECONCILE_INTERVAL_S:
            return
        self._last_reconcile_at = now_mono

        try:
            ex_side, ex_size, ex_entry = self._fetch_exchange_position(symbol)
        except Exception as e:
            self.log.warning(
                f"{symbol} | Reconcile: failed to fetch exchange position: {e}"
            )
            return

        position = self._positions.get(symbol)
        if not position:
            return

        int_side = position.side
        int_size = float(position.amount) if position.amount else 0.0

        ex_order_side = None
        if ex_side == "long" and ex_size > 0:
            ex_order_side = OrderSide.BUY
        elif ex_side == "short" and ex_size > 0:
            ex_order_side = OrderSide.SELL

        sides_match = (int_side == ex_order_side) or (
            int_side is None and ex_order_side is None
        )
        size_match = abs(int_size - ex_size) < 0.001

        if sides_match and size_match:
            self._reconcile_mismatch_count[symbol] = 0
            return

        prev_mismatches = self._reconcile_mismatch_count.get(symbol, 0)
        self._reconcile_mismatch_count[symbol] = prev_mismatches + 1

        self.log.warning(
            f"{symbol} | RECONCILE MISMATCH #{self._reconcile_mismatch_count[symbol]}: "
            f"Internal={int_side.value if int_side else 'FLAT'} {int_size:.4f} | "
            f"Exchange={ex_side or 'FLAT'} {ex_size:.4f} @ {ex_entry:.1f}"
        )

        if self._reconcile_mismatch_count[symbol] < 2:
            return

        self.log.error(
            f"{symbol} | RECONCILE: Correcting position state after 2 consecutive mismatches"
        )

        if ex_order_side is None and int_side is not None:
            self.log.error(
                f"{symbol} | RECONCILE: Exchange FLAT, clearing internal {int_side.value} state"
            )
            position.side = None
            position.entry_price = 0.0
            position.amount = Decimal("0")
            position.entry_time = None
            position.entry_bar = 0

        elif ex_order_side is not None and int_side is None:
            self.log.error(
                f"{symbol} | RECONCILE: Exchange has {ex_side} {ex_size}, "
                f"syncing internal state"
            )
            position.side = ex_order_side
            position.entry_price = ex_entry
            position.amount = Decimal(str(ex_size))
            position.entry_time = datetime.now(timezone.utc)

        elif ex_order_side != int_side:
            self.log.error(
                f"{symbol} | RECONCILE: Side mismatch! "
                f"Internal={int_side.value}, Exchange={ex_side}. Emergency close."
            )
            self._emergency_close_exchange(symbol, ex_side, ex_size, price)
            position.side = None
            position.entry_price = 0.0
            position.amount = Decimal("0")

        elif not size_match:
            self.log.error(
                f"{symbol} | RECONCILE: Size mismatch! "
                f"Internal={int_size:.4f}, Exchange={ex_size:.4f}. Syncing to exchange."
            )
            position.amount = Decimal(str(ex_size))
            position.entry_price = ex_entry

        if ex_size > self._MAX_POSITION_SIZE:
            self.log.error(
                f"{symbol} | EMERGENCY BRAKE: Position {ex_size:.4f} BTC exceeds "
                f"max {self._MAX_POSITION_SIZE}. Force closing!"
            )
            self._emergency_close_exchange(symbol, ex_side, ex_size, price)
            position.side = None
            position.entry_price = 0.0
            position.amount = Decimal("0")

        self._reconcile_mismatch_count[symbol] = 0

    def _fetch_exchange_position(self, symbol: str) -> tuple:
        """Fetch actual position from Bitget exchange via REST API."""
        raw_symbol = symbol.split("-")[0] if "-" in symbol else symbol.split(".")[0]

        base_url = "https://api.bitget.com"
        path = "/api/v2/mix/position/single-position"
        query = f"symbol={raw_symbol}&productType=USDT-FUTURES&marginCoin=USDT"

        ts = str(int(time.time() * 1000))
        message = ts + "GET" + path + "?" + query
        sign = base64.b64encode(
            hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "ACCESS-KEY": API_KEY,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": PASSPHRASE,
            "Content-Type": "application/json",
            "PAPTRADING": "1",
        }

        resp = _requests.get(base_url + path + "?" + query, headers=headers, timeout=10)
        data = resp.json()

        if data.get("code") != "00000":
            raise RuntimeError(f"API error: {data.get('code')} {data.get('msg')}")

        positions = data.get("data", [])
        if not positions:
            return (None, 0.0, 0.0)

        for pos in positions:
            total = float(pos.get("total", "0"))
            if total > 0:
                side = pos.get("holdSide", "")
                entry = float(pos.get("openPriceAvg", "0"))
                return (side, total, entry)

        return (None, 0.0, 0.0)

    def _emergency_close_exchange(
        self, symbol: str, side: str, size: float, price: float
    ) -> None:
        """Emergency close a position on exchange."""
        close_side = OrderSide.SELL if side == "long" else OrderSide.BUY
        amount = self.amount_to_precision(symbol, Decimal(str(size)))

        self.log.error(
            f"{symbol} | EMERGENCY CLOSE: {close_side.value} {amount} @ {price:.1f}"
        )

        self.create_order(
            symbol=symbol,
            side=close_side,
            type=OrderType.LIMIT,
            amount=amount,
            price=price,
        )

    def _check_out_of_grid(self, symbol: str, indicator, price: float) -> None:
        """Close position if price stays outside grid range for too long."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            self._out_of_grid_bars[symbol] = 0
            return

        if indicator.grid_lower is None or indicator.grid_upper is None:
            return

        is_outside = price < indicator.grid_lower or price > indicator.grid_upper

        if is_outside:
            self._out_of_grid_bars[symbol] = self._out_of_grid_bars.get(symbol, 0) + 1
            bars_out = self._out_of_grid_bars[symbol]
            self.log.warning(
                f"{symbol} | Price {price:.1f} OUTSIDE grid "
                f"[{indicator.grid_lower:.1f}-{indicator.grid_upper:.1f}] "
                f"for {bars_out}/{self._OUT_OF_GRID_MAX_BARS} bars"
            )
            if bars_out >= self._OUT_OF_GRID_MAX_BARS:
                self.log.warning(
                    f"{symbol} | OUT-OF-GRID AUTO-CLOSE triggered after {bars_out} bars"
                )
                self._close_grid_position(
                    symbol, f"Out-of-grid ({bars_out} bars)", price
                )
                self._out_of_grid_bars[symbol] = 0
        else:
            self._out_of_grid_bars[symbol] = 0

    def on_start(self) -> None:
        """Initialize strategy on start."""
        self.log.info("=" * 60)
        self.log.info("Starting Grid Trading Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Grid Config:")
        self.log.info(f"  grid_count: {self._GRID_COUNT}")
        self.log.info(f"  atr_multiplier: {self._ATR_MULTIPLIER}")
        self.log.info(f"  sma_period: {self._SMA_PERIOD}")
        self.log.info(f"  atr_period: {self._ATR_PERIOD}")
        self.log.info(f"  recalc_period: {self._RECALC_PERIOD}")
        self.log.info(f"  entry_lines: {self._ENTRY_LINES}")
        self.log.info(f"  profit_lines: {self._PROFIT_LINES}")
        self.log.info(f"  stop_loss: {self._STOP_LOSS_PCT * 100}%")
        self.log.info(f"  position_size: {self._POSITION_SIZE_PCT * 100}%")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = GridIndicator(
                grid_count=self._GRID_COUNT,
                atr_multiplier=self._ATR_MULTIPLIER,
                sma_period=self._SMA_PERIOD,
                atr_period=self._ATR_PERIOD,
                recalc_period=self._RECALC_PERIOD,
                entry_lines=self._ENTRY_LINES,
                profit_lines=self._PROFIT_LINES,
                kline_interval=KlineInterval.HOUR_1,
            )
            self._indicators[symbol] = indicator
            self._positions[symbol] = PositionState(symbol=symbol)
            self._bar_count[symbol] = 0

            self.subscribe_kline(symbol, KlineInterval.HOUR_1)
            self.subscribe_bookl1(symbol)

            self.register_indicator(
                symbols=symbol,
                indicator=indicator,
                data_type=DataType.KLINE,
            )

            self.log.info(f"Initialized tracking for {symbol}")
            self._manual_warmup(symbol, indicator)

        self._sync_positions_on_start()

    def on_kline(self, kline: Kline) -> None:
        """Process new kline data with grid trading logic.

        Completely overridden — grid's tick-based logic with burst detection,
        stale guard, spike rejection, bar detection, grid signal processing,
        reconciliation, and out-of-grid protection.
        """
        symbol = kline.symbol
        price = float(kline.close)
        now_mono = time.monotonic()

        # ===== GUARD 1: Skip first ticks (WS replay garbage) =====
        if not hasattr(self, "_tick_count"):
            self._tick_count = {}
        self._tick_count[symbol] = self._tick_count.get(symbol, 0) + 1
        if self._tick_count[symbol] <= 2:
            return

        # ===== GUARD 2: Tick burst detection (WS reconnect replay) =====
        if not hasattr(self, "_tick_timestamps"):
            self._tick_timestamps = {}
        if not hasattr(self, "_tick_burst_cooldown"):
            self._tick_burst_cooldown = {}

        cooldown_until = self._tick_burst_cooldown.get(symbol, 0.0)
        if now_mono < cooldown_until:
            return

        tick_times = self._tick_timestamps.get(symbol, [])
        tick_times.append(now_mono)
        tick_times = tick_times[-5:]
        self._tick_timestamps[symbol] = tick_times

        if len(tick_times) >= 3:
            time_span = tick_times[-1] - tick_times[-3]
            if time_span < 2.0:
                self._tick_burst_cooldown[symbol] = now_mono + 30.0
                self.log.warning(
                    f"{symbol} | Tick burst detected ({len(tick_times)} ticks in {time_span:.1f}s), "
                    f"cooldown 30s. Price={price:.1f}"
                )
                return

        # ===== GUARD 3: Stale data check on ALL ticks =====
        is_stale = self._is_kline_stale(kline)
        if is_stale:
            prev_count = self._stale_burst_count.get(symbol, 0)
            self._stale_burst_count[symbol] = prev_count + 1
            if self._stale_burst_count[symbol] == 1:
                self.log.warning(
                    f"{symbol} | STALE DATA detected (age={self._get_kline_age_s(kline):.0f}s), "
                    f"blocking ALL trading. Price={price:.1f}"
                )
            return

        if self._stale_burst_count.get(symbol, 0) > 0:
            burst_size = self._stale_burst_count[symbol]
            self.log.info(
                f"{symbol} | Stale burst ended ({burst_size} ticks), settling 30s..."
            )
            self._stale_burst_count[symbol] = 0
            self._burst_settle_until[symbol] = now_mono + 30.0

        settle_deadline = self._burst_settle_until.get(symbol, 0.0)
        if settle_deadline > 0.0 and now_mono < settle_deadline:
            return

        # ===== ALWAYS check stop-loss BEFORE spike rejection =====
        self._check_realtime_stops(symbol, price)

        # ===== GUARD 4: Price spike rejection =====
        indicator = self._indicators.get(symbol)
        if indicator and indicator.is_warmed_up and indicator.sma and indicator.sma > 0:
            sma = indicator.sma
            pct_from_sma = abs(price - sma) / sma
            if pct_from_sma > 0.05:
                self.log.warning(
                    f"{symbol} | Price spike rejected: {price:.1f} vs SMA {sma:.1f} "
                    f"({pct_from_sma:.1%} deviation)"
                )
                return

        # ===== Detect new bar =====
        bar_start = int(kline.start)
        if not hasattr(self, "_last_bar_start"):
            self._last_bar_start = {}
        prev_bar_start = self._last_bar_start.get(symbol, 0)
        is_new_bar = bar_start != prev_bar_start and prev_bar_start != 0
        self._last_bar_start[symbol] = bar_start

        if not indicator:
            return

        # Sync preserve_peak_trough flag
        position = self._positions.get(symbol)
        indicator._preserve_peak_trough = (
            position is not None and position.side is not None
        )

        indicator.handle_kline(kline)

        if not is_new_bar:
            # Same bar: grid signals + periodic tick log
            if self._check_live_ready(symbol):
                self._process_grid_signals(symbol, indicator, price)

            if self._check_live_ready(symbol):
                self._reconcile_position(symbol, price)

            if not hasattr(self, "_last_tick_log"):
                self._last_tick_log = {}
            last_tick_log = self._last_tick_log.get(symbol, 0.0)
            if now_mono - last_tick_log >= 30:
                self._last_tick_log[symbol] = now_mono
                pos_str = (
                    f"{position.side.value}@{position.entry_price:.0f}"
                    if position and position.side
                    else "FLAT"
                )
                grid_str = "N/A"
                if indicator.grid_lines is not None:
                    grid_str = (
                        f"[{indicator.grid_lower:.1f}-{indicator.grid_upper:.1f}]"
                    )
                self.log.info(
                    f"{symbol} | [tick] Price={price:.1f} | Grid={grid_str} | "
                    f"Level={indicator.current_level} | Pos={pos_str}"
                )
            return

        # ===== New bar detected =====
        if not indicator.is_warmed_up:
            self.log.info(f"{symbol} | Warming up...")
            return

        if symbol not in self._warmup_done_at:
            self._warmup_done_at[symbol] = now_mono
            self.log.info(f"{symbol} | Warmup complete, settling...")

        if not self._check_live_ready(symbol):
            return

        if not self._live_trading_ready.get(symbol):
            self._live_trading_ready[symbol] = True
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED")

        # Out-of-grid protection
        self._check_out_of_grid(symbol, indicator, price)

        # Grid trading logic
        self._process_grid_signals(symbol, indicator, price)

    def _process_grid_signals(
        self, symbol: str, indicator: GridIndicator, price: float
    ) -> None:
        """Process grid trading signals and execute orders."""
        position = self._positions.get(symbol)
        if not position or indicator.grid_lines is None:
            return

        current_level = indicator.current_level
        peak_level = indicator.peak_level
        trough_level = indicator.trough_level

        # Log grid state only when level changes
        last_logged_level = getattr(self, "_last_logged_level", {}).get(symbol)
        if last_logged_level != current_level:
            if not hasattr(self, "_last_logged_level"):
                self._last_logged_level = {}
            self._last_logged_level[symbol] = current_level
            self.log.info(
                f"{symbol} | Grid: SMA={indicator.sma:.1f} ATR={indicator.atr:.1f} "
                f"Range=[{indicator.grid_lower:.1f}-{indicator.grid_upper:.1f}] "
                f"Level={current_level} Peak={peak_level} Trough={trough_level}"
            )

        # Check stop loss first
        if position.side is not None:
            if self._should_stop_loss(
                position.entry_price, price, position.side == OrderSide.BUY
            ):
                self.log.warning(f"STOP LOSS triggered for {symbol}")
                self._close_grid_position(symbol, "Stop loss", price)
                return

        if position.side is None:
            # FLAT - look for entry signals
            if (
                peak_level - current_level >= self._ENTRY_LINES
                and current_level <= self._GRID_COUNT // 2
            ):
                self._open_grid_position(symbol, OrderSide.BUY, price, current_level)
                return

            if (
                current_level - trough_level >= self._ENTRY_LINES
                and current_level >= self._GRID_COUNT // 2
            ):
                self._open_grid_position(symbol, OrderSide.SELL, price, current_level)
                return
        else:
            # In position - look for exit signals
            if (
                position.side == OrderSide.BUY
                and current_level - trough_level >= self._PROFIT_LINES
            ):
                self.log.info(
                    f"LONG profit target hit: level rose {current_level - trough_level} from trough"
                )
                self._close_grid_position(symbol, "Long profit target", price)
                return

            if (
                position.side == OrderSide.SELL
                and peak_level - current_level >= self._PROFIT_LINES
            ):
                self.log.info(
                    f"SHORT profit target hit: level fell {peak_level - current_level} from peak"
                )
                self._close_grid_position(symbol, "Short profit target", price)
                return

    def _open_grid_position(
        self, symbol: str, side: OrderSide, price: float, level: int
    ) -> None:
        """Open a new grid position (LIMIT order)."""
        amount = self._calculate_position_size(symbol, price)
        if amount <= 0:
            self.log.warning(f"Cannot open {side.value} position: amount too small")
            return

        self.log.info(
            f">>> OPENING {side.value} position: {symbol} @ {price:.2f} "
            f"(level {level}), size={amount}"
        )

        self.create_order(
            symbol=symbol,
            side=side,
            type=OrderType.LIMIT,
            amount=amount,
            price=price,
        )

        position = self._positions[symbol]
        position.side = side
        position.entry_price = price
        position.amount = amount
        position.entry_time = datetime.now(timezone.utc)
        position.entry_bar = level  # Grid uses entry_bar to store entry_level

        # Reset peak/trough
        indicator = self._indicators.get(symbol)
        if indicator:
            indicator._peak_level = indicator.current_level
            indicator._trough_level = indicator.current_level

    def _close_grid_position(
        self, symbol: str, reason: str, price: float = 0.0
    ) -> None:
        """Close existing grid position (LIMIT order)."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return

        self.log.info(f"<<< CLOSING position: {symbol} @ {price:.2f}, reason={reason}")

        close_side = OrderSide.SELL if position.side == OrderSide.BUY else OrderSide.BUY

        self.create_order(
            symbol=symbol,
            side=close_side,
            type=OrderType.LIMIT,
            amount=position.amount,
            price=price,
        )

        position.side = None
        position.entry_price = 0.0
        position.amount = Decimal("0")
        position.entry_time = None
        position.entry_bar = 0

        # Reset peak/trough tracking
        indicator = self._indicators.get(symbol)
        if indicator:
            indicator._peak_level = indicator.current_level
            indicator._trough_level = indicator.current_level

    # Order callbacks (inherited from base, but grid doesn't track daily_stats.trade_count)
    def on_filled_order(self, order: Order) -> None:
        self.log.info(
            f"Order FILLED: {order.symbol} {order.side} {order.amount} @ {order.price}"
        )


# Create and configure strategy
def create_strategy(*, account_type) -> GridStrategy:
    """Create and configure the grid strategy."""
    strategy = GridStrategy(symbols=["BTCUSDT-PERP.BITGET"], account_type=account_type)
    return strategy


# Main execution
if __name__ == "__main__":
    # Clear log file on restart
    LOG_DIR = Path(__file__).parent
    GRID_LOG_FILE = LOG_DIR / "grid.log"
    if GRID_LOG_FILE.exists():
        GRID_LOG_FILE.write_text("")

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

    strategy = create_strategy(account_type=BitgetAccountType.UTA_DEMO)

    # Engine configuration
    config = Config(
        strategy_id="grid_trading",
        user_id="user_test",
        strategy=strategy,
        log_config=LogConfig(
            level_stdout="INFO",
            level_file="INFO",
            directory=str(Path(__file__).parent),
            file_name="grid.log",
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

    print(f"Log file: {LOG_FILE}")
    try:
        engine.start()
    finally:
        engine.dispose()
        if hasattr(sys.stdout, "close"):
            sys.stdout.close()
