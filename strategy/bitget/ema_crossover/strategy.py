"""
EMA Crossover Trading Strategy for Bitget.

This strategy uses:
- Fast/Slow EMA crossover for signal generation (golden cross / death cross)
- Price-based stop loss (handled by SignalCore in live mode)
- Trade filtering (min holding, cooldown, signal confirmation)

Usage:
    uv run python -m strategy.bitget.ema_crossover.strategy

Log output:
    strategy/bitget/ema_crossover/strategy_output.log
"""

import sys
from pathlib import Path
from typing import List, Optional

from strategy.bitget.common.base_strategy import LogTee

# Set up log file in the strategy directory
LOG_FILE = Path(__file__).parent / "strategy_output.log"
sys.stdout = LogTee(str(LOG_FILE))

from nexustrader.config import (
    BasicConfig,
    Config,
    LogConfig,
    PrivateConnectorConfig,
    PublicConnectorConfig,
)
from nexustrader.constants import (
    ExchangeType,
    KlineInterval,
    settings,
)
from nexustrader.engine import Engine
from nexustrader.exchange import BitgetAccountType
from nexustrader.indicator import Indicator

from strategy.bitget.common.base_strategy import BaseQuantStrategy, PositionState
from strategy.bitget.ema_crossover.configs import (
    EMATradeFilterConfig,
    get_config,
)
from strategy.strategies.ema_crossover.core import EMAConfig
from strategy.bitget.ema_crossover.indicator import EMACrossoverIndicator


# API credentials from settings
API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE


class EMACrossoverStrategy(BaseQuantStrategy):
    """
    EMA Crossover quantitative trading strategy.

    Uses fast/slow EMA crossover for trend-following signals:
    - Golden cross (fast > slow): BUY
    - Death cross (fast < slow): SELL
    - Stop loss, cooldown, signal confirmation handled by SignalCore
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[EMAConfig] = None,
        filter_config: Optional[EMATradeFilterConfig] = None,
    ):
        super().__init__()
        config = config or EMAConfig()
        filter_config = filter_config or EMATradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
        )

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting EMA Crossover Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  fast_period: {self._config.fast_period}")
        self.log.info(f"  slow_period: {self._config.slow_period}")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100}%")
        self.log.info("Filter Config:")
        self.log.info(
            f"  min_holding_bars: {self._filter.min_holding_bars} "
            f"({self._filter.min_holding_bars * 15} min)"
        )
        self.log.info(
            f"  cooldown_bars: {self._filter.cooldown_bars} "
            f"({self._filter.cooldown_bars * 15} min)"
        )
        self.log.info(f"  signal_confirmation: {self._filter.signal_confirmation}")
        self.log.info("=" * 60)

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = EMACrossoverIndicator(
                config=self._config,
                kline_interval=KlineInterval.MINUTE_15,
                min_holding_bars=self._filter.min_holding_bars,
                cooldown_bars=self._filter.cooldown_bars,
                signal_confirmation=self._filter.signal_confirmation,
            )
            self._register_symbol(symbol, indicator)
            self.log.info(f"Initialized tracking for {symbol}")

    def _format_log_line(
        self,
        symbol: str,
        signal,
        position: PositionState,
        indicator: Indicator,
        current_bar: int,
    ) -> str:
        fast_str = f"{indicator.fast_ema:.1f}" if indicator.fast_ema else "N/A"
        slow_str = f"{indicator.slow_ema:.1f}" if indicator.slow_ema else "N/A"
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )
        return (
            f"{symbol} | Bar={current_bar} | Fast={fast_str} | Slow={slow_str} | "
            f"Signal={signal.value} | Pos={pos_str}"
        )


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
# =============================================================================
# Mesa #0 = best Sharpe (default)
# Mesa #1 = second best, etc.
#
# Generate configs: uv run python strategy/bitget/ema_crossover/backtest.py --heatmap
# List configs:     python -m strategy.bitget.ema_crossover.configs
# Override:         python -m strategy.bitget.ema_crossover.strategy --mesa 1
# =============================================================================

import argparse as _argparse

_parser = _argparse.ArgumentParser(add_help=False)
_parser.add_argument("--mesa", type=int, default=0, help="Mesa index (0=best)")
_args, _ = _parser.parse_known_args()

# Load configuration from Mesa configs (heatmap_results.json)
selected = get_config(_args.mesa)
strategy_config, filter_config = selected.get_configs()

# Clear log file on restart
LOG_DIR = Path(__file__).parent
EMA_LOG_FILE = LOG_DIR / "ema_crossover.log"
if EMA_LOG_FILE.exists():
    EMA_LOG_FILE.write_text("")

# Create strategy instance
strategy = EMACrossoverStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"ema_crossover_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="ema_crossover.log",
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
