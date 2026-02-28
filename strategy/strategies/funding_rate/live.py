"""
Funding Rate Arbitrage Trading Strategy.

This strategy:
- Monitors BTC perpetual contract funding rates
- Opens short positions before 8h funding settlements when rates are positive
- Collects funding payments as profit
- Closes positions after settlement to minimize directional risk
- Uses SMA trend filter to avoid fighting strong uptrends
- 5x leverage with 3% hard stop-loss

Usage:
    uv run python -m strategy.strategies.funding_rate.live

Log output:
    strategy/strategies/funding_rate/strategy_output.log
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from strategy.strategies._base.base_strategy import LogTee

# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "strategy_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.constants import (  # noqa: E402
    ExchangeType,
    KlineInterval,
    OrderSide,
)
from nexustrader.indicator import Indicator  # noqa: E402
from nexustrader.schema import FundingRate  # noqa: E402

from strategy.strategies._base.base_strategy import (  # noqa: E402
    BaseQuantStrategy,
    PositionState,
    _CLOSE,
    _SELL,
)
from strategy.strategies.funding_rate.configs import (  # noqa: E402
    FundingRateFilterConfig,
    get_config,
)
from strategy.strategies.funding_rate.core import FundingRateConfig  # noqa: E402
from strategy.strategies.funding_rate.indicator import (  # noqa: E402
    FundingRateIndicator,
)


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


class FundingRateArbitrageStrategy(BaseQuantStrategy):
    """
    Funding Rate Arbitrage quantitative trading strategy.

    Collects funding payments by shorting perpetual contracts when
    funding rate is positive:
    - Enter short before 8h settlement -> collect positive funding
    - Exit after settlement -> minimize directional risk
    - SMA trend filter to avoid fighting strong uptrends
    - 3% hard stop-loss for capital protection
    """

    _ENABLE_STALE_GUARD: bool = True
    _MAX_KLINE_AGE_S: float = 120.0  # 2 minutes for 1h candles

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[FundingRateConfig] = None,
        filter_config: Optional[FundingRateFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or FundingRateConfig()
        filter_config = filter_config or FundingRateFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )
        self._stats_log_interval = 4  # Every 4 hours (4 * 1h)

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
        self.log.info(
            f"  max_adverse_move: {self._config.max_adverse_move_pct * 100:.1f}%"
        )
        self.log.info(f"  position_size: {self._config.position_size_pct * 100:.0f}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100:.1f}%")
        self.log.info(f"  hours_before_funding: {self._config.hours_before_funding}")
        self.log.info(f"  hours_after_funding: {self._config.hours_after_funding}")
        self.log.info("Filter Config:")
        self.log.info(f"  min_holding_bars: {self._filter.min_holding_bars}")
        self.log.info(f"  cooldown_bars: {self._filter.cooldown_bars}")
        self.log.info("=" * 60)

        self._reset_daily_stats()
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

            # Subscribe to klines for SMA and price tracking
            self.subscribe_kline(symbol, KlineInterval.HOUR_1)
            self.subscribe_bookl1(symbol)

            # Subscribe to funding rate updates
            self.subscribe_funding_rate(symbols=symbol)

            self.register_indicator(
                symbols=symbol,
                indicator=indicator,
                data_type="kline",
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

    def _get_signal(self, symbol: str, indicator: Indicator):
        """Override: pass funding timing args to indicator."""
        now_utc = datetime.now(timezone.utc)
        return indicator.get_signal(
            _hours_until_next_settlement_utc(now_utc),
            _hours_since_last_settlement_utc(now_utc),
        )

    def _check_stop_loss(self, symbol: str, indicator: Indicator, price: float) -> bool:
        """Override: funding_rate's should_stop_loss doesn't take is_long."""
        position = self._positions.get(symbol)
        if not position or position.side is None:
            return False
        if hasattr(indicator, "should_stop_loss"):
            if indicator.should_stop_loss(position.entry_price, price):
                self.log.warning(f"STOP LOSS triggered for {symbol}")
                self._close_position(symbol, "Stop loss", force=True)
                return True
        return False

    def _process_signal(
        self, symbol: str, signal, price: float, current_bar: int
    ) -> None:
        """Override: short-only (only SELL and CLOSE, no BUY, no signal confirmation)."""
        position = self._positions.get(symbol)
        if not position:
            return

        if self._is_in_cooldown(symbol):
            return

        sig = signal.value

        # Close signal (after settlement)
        if sig == _CLOSE:
            if position.side is not None and self._can_close_position(symbol):
                self._close_position(symbol, "Post-settlement close")
            return

        # Sell signal (open short before settlement)
        if sig == _SELL:
            if position.side is None:
                self._open_position(symbol, OrderSide.SELL, price, current_bar)

    def _format_log_line(
        self,
        symbol: str,
        signal,
        position: PositionState,
        indicator: Indicator,
        current_bar: int,
    ) -> str:
        bars_held = (
            current_bar - position.entry_bar if position and position.side else 0
        )
        pos_str = (
            f"SHORT@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )

        now_utc = datetime.now(timezone.utc)
        hours_to_next = _hours_until_next_settlement_utc(now_utc)
        hours_since_last = _hours_since_last_settlement_utc(now_utc)

        return (
            f"{symbol} | Bar={current_bar} | SMA={indicator.sma:.1f} | "
            f"FR={indicator.avg_funding_rate * 100:.6f}% | Signal={signal.value} | "
            f"Pos={pos_str} | Hold={bars_held} | "
            f"NextSettlement={hours_to_next:.1f}h | SinceSettlement={hours_since_last:.1f}h"
        )


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
strategy = FundingRateArbitrageStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
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
