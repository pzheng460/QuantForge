"""
VWAP Mean Reversion Trading Strategy.

This strategy uses:
- VWAP (Volume Weighted Average Price) with daily reset at 00:00 UTC
- Z-Score of price deviation from VWAP for entry/exit signals
- RSI (14) as secondary confirmation filter
- 5-minute candle timeframe

Usage:
    uv run python -m strategy.strategies.vwap.live

Log output:
    strategy/strategies/vwap/strategy_output.log
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
from strategy.strategies.vwap.configs import (  # noqa: E402
    VWAPTradeFilterConfig,
    get_config,
)
from strategy.strategies.vwap.core import VWAPConfig  # noqa: E402
from strategy.strategies.vwap.indicator import (  # noqa: E402
    VWAPIndicator,
)


class VWAPMeanReversionStrategy(BaseQuantStrategy):
    """
    VWAP mean reversion quantitative trading strategy.

    Uses VWAP + Z-Score + RSI for mean-reversion signals:
    - Z <= -entry AND RSI < oversold: BUY (price below VWAP)
    - Z >= entry AND RSI > overbought: SELL (price above VWAP)
    - |Z| < exit_threshold: CLOSE (price returned to VWAP)
    - |Z| >= zscore_stop OR price > stop_loss_pct: STOP LOSS
    """

    _ENABLE_STALE_GUARD: bool = True
    _MAX_KLINE_AGE_S: float = 60.0

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[VWAPConfig] = None,
        filter_config: Optional[VWAPTradeFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or VWAPConfig()
        filter_config = filter_config or VWAPTradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )
        self._stats_log_interval = 12  # Every hour (12 * 5min)

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

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = VWAPIndicator(
                config=self._config,
                kline_interval=KlineInterval.MINUTE_5,
            )
            self._register_symbol(symbol, indicator, KlineInterval.MINUTE_5)
            self.log.info(f"Initialized tracking for {symbol}")

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
        vwap_str = f"{indicator.vwap:.1f}" if indicator.vwap else "N/A"

        return (
            f"{symbol} | Bar={current_bar} | VWAP={vwap_str} | Z={indicator.zscore:.2f} | "
            f"RSI={indicator.rsi:.1f} | Signal={signal.value} | Pos={pos_str} | "
            f"Hold={bars_held} | CD={cooldown_remaining}"
        )


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
# =============================================================================
# Mesa #0 = best Sharpe (default)
# Mesa #1 = second best, etc.
#
# Generate configs: uv run python -m strategy.backtest -S vwap -X bitget --heatmap
# List configs:     python -m strategy.strategies.vwap.configs
# Override:         python -m strategy.strategies.vwap.live --mesa 1
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
    print(
        "No usable Mesa configs found. Using optimized VWAP config from heatmap scan."
    )
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
strategy = VWAPMeanReversionStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
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
