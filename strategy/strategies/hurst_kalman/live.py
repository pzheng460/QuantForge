"""
Hurst-Kalman Trading Strategy (V2 - Optimized).

This strategy uses:
- Hurst exponent for market state identification (mean-reverting vs trending)
- Kalman filter for true value estimation
- Z-Score for signal generation
- Trade filtering (min holding, cooldown, signal confirmation)

Usage:
    uv run python -m strategy.strategies.hurst_kalman.live

Log output:
    strategy/strategies/hurst_kalman/strategy_output.log
"""

import sys
from pathlib import Path
from typing import List, Optional

from strategy.strategies._base.base_strategy import LogTee

# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "strategy_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.constants import (  # noqa: E402
    ExchangeType,
    KlineInterval,
)
from nexustrader.indicator import Indicator  # noqa: E402

from strategy.strategies._base.base_strategy import BaseQuantStrategy, PositionState  # noqa: E402
from strategy.strategies.hurst_kalman.configs import (  # noqa: E402
    TradeFilterConfig,
    get_config,
)
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig  # noqa: E402
from strategy.strategies.hurst_kalman.indicator import (  # noqa: E402
    HurstKalmanIndicator,
    MarketState,
)


class HurstKalmanStrategy(BaseQuantStrategy):
    """
    Hurst-Kalman quantitative trading strategy (V2 - Optimized).

    Key improvements over V1:
    1. Minimum holding period to reduce overtrading
    2. Cooldown after closing positions
    3. Signal confirmation for entry
    4. Only trades in mean-reversion regime (most robust)
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[HurstKalmanConfig] = None,
        filter_config: Optional[TradeFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or HurstKalmanConfig()
        filter_config = filter_config or TradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting Hurst-Kalman Strategy V2 (Optimized)")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  zscore_entry: {self._config.zscore_entry}")
        self.log.info(
            f"  mean_reversion_threshold: {self._config.mean_reversion_threshold}"
        )
        self.log.info(f"  kalman_R: {self._config.kalman_R}")
        self.log.info(f"  kalman_Q: {self._config.kalman_Q}")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100}%")
        self.log.info("Filter Config:")
        self.log.info(
            f"  min_holding_bars: {self._filter.min_holding_bars} ({self._filter.min_holding_bars * 15} min)"
        )
        self.log.info(
            f"  cooldown_bars: {self._filter.cooldown_bars} ({self._filter.cooldown_bars * 15} min)"
        )
        self.log.info(f"  signal_confirmation: {self._filter.signal_confirmation}")
        self.log.info(f"  only_mean_reversion: {self._filter.only_mean_reversion}")
        self.log.info("=" * 60)

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = HurstKalmanIndicator(
                config=self._config,
                kline_interval=KlineInterval.MINUTE_15,
            )
            self._register_symbol(symbol, indicator)
            self.log.info(f"Initialized tracking for {symbol}")

    def _process_signal(
        self, symbol: str, signal, price: float, current_bar: int
    ) -> None:
        """Override: adds only_mean_reversion regime filter."""
        if self._filter.only_mean_reversion:
            indicator = self._indicators.get(symbol)
            if indicator and indicator.market_state != MarketState.MEAN_REVERTING:
                position = self._positions.get(symbol)
                if (
                    position
                    and position.side is not None
                    and self._can_close_position(symbol)
                ):
                    self._close_position(symbol, "Left mean-reversion regime")
                return
        super()._process_signal(symbol, signal, price, current_bar)

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
        cooldown_remaining = max(0, self._cooldown_until.get(symbol, 0) - current_bar)
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )

        return (
            f"{symbol} | Bar={current_bar} | H={indicator.hurst:.3f} | "
            f"Z={indicator.zscore:+.2f} | State={indicator.market_state.value} | "
            f"Signal={signal.value} | Pos={pos_str} | Hold={bars_held} | CD={cooldown_remaining}"
        )


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
# =============================================================================
# Mesa #0 = best Sharpe (default)
# Mesa #1 = second best, etc.
#
# Generate configs: uv run python strategy/strategies/hurst_kalman/backtest.py --heatmap
# List configs:     python -m strategy.strategies.hurst_kalman.configs
# Override:         python -m strategy.strategies.hurst_kalman.live --mesa 1
# =============================================================================

import argparse as _argparse  # noqa: E402

_parser = _argparse.ArgumentParser(add_help=False)
_parser.add_argument("--mesa", type=int, default=0, help="Mesa index (0=best)")
_args, _ = _parser.parse_known_args()

# Load configuration from Mesa configs (heatmap_results.json)
selected = get_config(_args.mesa)
strategy_config, filter_config = selected.get_configs()

# Clear log file on restart
LOG_DIR = Path(__file__).parent
HURST_LOG_FILE = LOG_DIR / "hurst_kalman.log"
if HURST_LOG_FILE.exists():
    HURST_LOG_FILE.write_text("")

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
strategy = HurstKalmanStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"hurst_kalman_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="hurst_kalman.log",
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
                leverage_symbols=["BTCUSDT-PERP.BITGET"],
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
