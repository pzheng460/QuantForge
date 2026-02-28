"""
Bollinger Band Mean Reversion Trading Strategy.

This strategy uses:
- Bollinger Bands for overbought/oversold detection
- Mean reversion to SMA for exit signals
- Price-based stop loss for risk management

Usage:
    uv run python -m strategy.strategies.bollinger_band.live

Log output:
    strategy/strategies/bollinger_band/strategy_output.log
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
from strategy.strategies.bollinger_band.configs import (  # noqa: E402
    BBTradeFilterConfig,
    get_config,
)
from strategy.strategies.bollinger_band.core import BBConfig  # noqa: E402
from strategy.strategies.bollinger_band.indicator import (  # noqa: E402
    BollingerBandIndicator,
)


class BollingerBandStrategy(BaseQuantStrategy):
    """
    Bollinger Band mean reversion quantitative trading strategy.

    Uses Bollinger Bands for mean-reversion signals:
    - Price below lower band (oversold): BUY
    - Price above upper band (overbought): SELL
    - Price returns to SMA: CLOSE
    - Price-based stop loss for risk management
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[BBConfig] = None,
        filter_config: Optional[BBTradeFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or BBConfig()
        filter_config = filter_config or BBTradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting Bollinger Band Mean Reversion Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  bb_period: {self._config.bb_period}")
        self.log.info(f"  bb_multiplier: {self._config.bb_multiplier}")
        self.log.info(f"  exit_threshold: {self._config.exit_threshold}")
        self.log.info(f"  trend_bias: {self._config.trend_bias or 'none (both sides)'}")
        self.log.info(f"  trend_sma_multiplier: {self._config.trend_sma_multiplier}")
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
        self.log.info("=" * 60)

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = BollingerBandIndicator(
                config=self._config,
                kline_interval=KlineInterval.MINUTE_15,
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
        bars_held = (
            current_bar - position.entry_bar if position and position.side else 0
        )
        cooldown_remaining = max(0, self._cooldown_until.get(symbol, 0) - current_bar)
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )
        sma_str = f"{indicator.sma:.1f}" if indicator.sma else "N/A"
        pctb_str = f"{indicator.pct_b:.2f}" if indicator.pct_b is not None else "N/A"
        bias_str = self._config.trend_bias or "both"

        return (
            f"{symbol} | Bar={current_bar} | SMA={sma_str} | %B={pctb_str} | "
            f"Bias={bias_str} | Signal={signal.value} | Pos={pos_str} | "
            f"Hold={bars_held} | CD={cooldown_remaining}"
        )


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
# =============================================================================
# Mesa #0 = best Sharpe (default)
# Mesa #1 = second best, etc.
#
# Generate configs: uv run python strategy/strategies/bollinger_band/backtest.py --heatmap
# List configs:     python -m strategy.strategies.bollinger_band.configs
# Override:         python -m strategy.strategies.bollinger_band.live --mesa 1
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
BB_LOG_FILE = LOG_DIR / "bollinger_band.log"
if BB_LOG_FILE.exists():
    BB_LOG_FILE.write_text("")

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
strategy = BollingerBandStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"bollinger_band_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="bollinger_band.log",
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
