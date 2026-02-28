"""
EMA Crossover + Regime Filter Trading Strategy.

This strategy uses:
- Fast/Slow EMA crossover for signal generation
- ATR + ADX regime detection to filter out ranging markets
- Price-based stop loss
- 1-hour candle timeframe

Usage:
    uv run python -m strategy.strategies.regime_ema.live

Log output:
    strategy/strategies/regime_ema/strategy_output.log
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
from strategy.strategies.regime_ema.configs import (  # noqa: E402
    RegimeEMATradeFilterConfig,
    get_config,
)
from strategy.strategies.regime_ema.core import (  # noqa: E402
    RegimeEMAConfig,
)
from strategy.strategies.regime_ema.indicator import (  # noqa: E402
    RegimeEMAIndicator,
)


class RegimeEMAStrategy(BaseQuantStrategy):
    """
    EMA Crossover + Regime Filter quantitative trading strategy.

    Uses ATR + ADX to detect market regime, then applies EMA crossover
    signals only during trending markets.  Automatically flattens
    positions when the market enters a ranging state.

    - TRENDING_UP / TRENDING_DOWN: trade EMA crossovers
    - RANGING: close all positions, log "REGIME: RANGING - skipping trades"
    - HIGH_VOLATILITY: close all positions
    """

    _ENABLE_STALE_GUARD: bool = True
    _MAX_KLINE_AGE_S: float = 120.0  # 2 min for 1h candles

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        config: Optional[RegimeEMAConfig] = None,
        filter_config: Optional[RegimeEMATradeFilterConfig] = None,
        *,
        account_type,
    ):
        super().__init__()
        config = config or RegimeEMAConfig()
        filter_config = filter_config or RegimeEMATradeFilterConfig()
        self._init_common(
            symbols=symbols or config.symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )
        self._stats_log_interval = 1  # 1h candles — log every bar

    def on_start(self) -> None:
        """Initialize subscriptions and indicators on strategy start."""
        self.log.info("=" * 60)
        self.log.info("Starting EMA Crossover + Regime Filter Strategy")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")
        self.log.info("Strategy Config:")
        self.log.info(f"  fast_period: {self._config.fast_period}")
        self.log.info(f"  slow_period: {self._config.slow_period}")
        self.log.info(f"  atr_period: {self._config.atr_period}")
        self.log.info(f"  adx_period: {self._config.adx_period}")
        self.log.info(f"  adx_trend_threshold: {self._config.adx_trend_threshold}")
        self.log.info(f"  trend_atr_threshold: {self._config.trend_atr_threshold}")
        self.log.info(f"  ranging_atr_threshold: {self._config.ranging_atr_threshold}")
        self.log.info(f"  regime_lookback: {self._config.regime_lookback}")
        self.log.info(f"  position_size: {self._config.position_size_pct * 100}%")
        self.log.info(f"  stop_loss: {self._config.stop_loss_pct * 100}%")
        self.log.info("Filter Config:")
        self.log.info(
            f"  min_holding_bars: {self._filter.min_holding_bars} "
            f"({self._filter.min_holding_bars * 60} min)"
        )
        self.log.info(
            f"  cooldown_bars: {self._filter.cooldown_bars} "
            f"({self._filter.cooldown_bars * 60} min)"
        )
        self.log.info(f"  signal_confirmation: {self._filter.signal_confirmation}")
        self.log.info("=" * 60)

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        for symbol in self._symbols:
            indicator = RegimeEMAIndicator(
                config=self._config,
                kline_interval=KlineInterval.HOUR_1,
            )
            self._register_symbol(symbol, indicator, KlineInterval.HOUR_1)
            self.log.info(f"Initialized tracking for {symbol}")

    def _pre_signal_hook(
        self, symbol: str, signal, price: float, indicator: Indicator, current_bar: int
    ) -> bool:
        """Regime filter: close positions in ranging/high-vol, skip signal processing."""
        if not indicator.is_trending:
            position = self._positions.get(symbol)
            regime = indicator.regime
            if position and position.side is not None:
                self.log.info(
                    f"REGIME: {regime.value.upper()} - skipping trades, "
                    f"closing position for {symbol}"
                )
                self._close_position(symbol, f"Regime={regime.value}", force=True)
            else:
                if current_bar % 6 == 0:  # Log every ~6 hours
                    self.log.info(f"REGIME: {regime.value.upper()} - skipping trades")
            return True  # Skip _process_signal in ranging market
        return False

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
        fast_str = f"{indicator.fast_ema:.1f}" if indicator.fast_ema else "N/A"
        slow_str = f"{indicator.slow_ema:.1f}" if indicator.slow_ema else "N/A"
        atr_str = f"{indicator.atr:.1f}" if indicator.atr else "N/A"
        adx_str = f"{indicator.adx:.1f}" if indicator.adx else "N/A"
        regime = indicator.regime

        return (
            f"{symbol} | Bar={current_bar} | Fast={fast_str} | Slow={slow_str} | "
            f"ATR={atr_str} | ADX={adx_str} | Regime={regime.value} | "
            f"Signal={signal.value} | Pos={pos_str} | Hold={bars_held} | "
            f"CD={cooldown_remaining}"
        )


# =============================================================================
# CONFIGURATION SELECTION (Mesa index from heatmap scan)
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
    print("No usable Mesa configs found. Using default RegimeEMA config.")
    from strategy.backtest.config import StrategyConfig as _SC

    strategy_config = RegimeEMAConfig()
    filter_config = RegimeEMATradeFilterConfig()
    selected = _SC(
        name="Default",
        description="Default RegimeEMA config (no heatmap data)",
        strategy_config=strategy_config,
        filter_config=filter_config,
    )

# Clear log file on restart
LOG_DIR = Path(__file__).parent
REGIME_LOG_FILE = LOG_DIR / "regime_ema.log"
if REGIME_LOG_FILE.exists():
    REGIME_LOG_FILE.write_text("")

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
strategy = RegimeEMAStrategy(
    symbols=["BTCUSDT-PERP.BITGET"],
    config=strategy_config,
    filter_config=filter_config,
    account_type=BitgetAccountType.UTA_DEMO,
)
strategy.set_config_info(_args.mesa, selected.name)

# Engine configuration
config = Config(
    strategy_id=f"regime_ema_{selected.name.lower().replace(' ', '_')}",
    user_id="user_test",
    strategy=strategy,
    log_config=LogConfig(
        level_stdout="INFO",
        level_file="INFO",
        directory=str(Path(__file__).parent),
        file_name="regime_ema.log",
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
