"""
Generic live trading strategy that works with any registered strategy.

Uses the registration system's LiveConfig to configure itself, eliminating
the need for per-strategy live.py files. All 9 strategies are supported.

Usage:
    from strategy.strategies._base.generic_strategy import GenericStrategy

    strategy = GenericStrategy(
        strategy_name="ema_crossover",
        symbols=["BTCUSDT-PERP.BITGET"],
        config=ema_config,
        filter_config=filter_config,
        account_type=BitgetAccountType.UTA_DEMO,
    )
"""

import dataclasses
from typing import Any, List, Optional

from nexustrader.indicator import Indicator
from nexustrader.schema import FundingRate

from strategy.backtest.registry import LiveConfig, get_strategy
from strategy.strategies._base.base_strategy import BaseQuantStrategy, PositionState
from strategy.strategies._base.generic_indicator import GenericIndicator


class GenericStrategy(BaseQuantStrategy):
    """Generic live strategy that works with any registered strategy.

    Configured via LiveConfig from the strategy registration.
    Handles on_start, on_kline (via base template), signal processing,
    and all standard lifecycle hooks.
    """

    def __init__(
        self,
        strategy_name: str,
        symbols: Optional[List[str]] = None,
        config: Any = None,
        filter_config: Any = None,
        *,
        account_type,
    ):
        super().__init__()

        # Look up registration
        self._registration = get_strategy(strategy_name)
        self._strategy_name = strategy_name

        live_config = self._registration.live_config
        if live_config is None:
            raise ValueError(
                f"Strategy '{strategy_name}' does not have a LiveConfig. "
                f"Use the strategy's custom live.py instead."
            )
        self._live_config: LiveConfig = live_config

        # Set class-level flags from LiveConfig
        if live_config.enable_stale_guard:
            self._ENABLE_STALE_GUARD = True
            self._MAX_KLINE_AGE_S = live_config.max_kline_age_s

        # Default configs from registration if not provided
        config = config or self._registration.config_cls()
        filter_config = filter_config or self._registration.filter_config_cls()

        resolved_symbols = (
            symbols or getattr(config, "symbols", None) or [live_config.default_symbol]
        )

        self._init_common(
            symbols=resolved_symbols,
            config=config,
            filter_config=filter_config,
            account_type=account_type,
        )

        # Compute warmup period
        if live_config.warmup_fn:
            self._warmup_period = live_config.warmup_fn(config)
        else:
            self._warmup_period = _default_warmup(config)

    def on_start(self) -> None:
        """Initialize subscriptions and indicators."""
        display = self._registration.display_name
        self.log.info("=" * 60)
        self.log.info(f"Starting {display} Strategy (Generic Runner)")
        self.log.info("=" * 60)
        self.log.info(f"Symbols: {self._symbols}")

        # Log config fields
        self.log.info("Strategy Config:")
        for f in dataclasses.fields(self._config):
            val = getattr(self._config, f.name)
            if f.name != "symbols":
                self.log.info(f"  {f.name}: {val}")

        self.log.info("Filter Config:")
        for f in dataclasses.fields(self._filter):
            self.log.info(f"  {f.name}: {getattr(self._filter, f.name)}")
        self.log.info("=" * 60)

        self._reset_daily_stats()
        self.log.info("Performance Tracker will initialize when balance is available")
        self.log.info("=" * 60)

        lc = self._live_config
        interval = self._registration.default_interval

        # Build filter params dict for dual-mode cores
        filter_dict = {}
        if lc.use_dual_mode:
            for f in dataclasses.fields(self._filter):
                filter_dict[f.name] = getattr(self._filter, f.name)

        for symbol in self._symbols:
            indicator = GenericIndicator(
                core_cls=lc.core_cls,
                config=self._config,
                update_columns=lc.update_columns,
                warmup_period_bars=self._warmup_period,
                kline_interval=interval,
                filter_params=filter_dict,
                use_dual_mode=lc.use_dual_mode,
                pre_update_hook=lc.pre_update_hook,
            )
            self._register_symbol(symbol, indicator, interval)

            if lc.subscribe_funding_rate:
                self.subscribe_funding_rate(symbols=symbol)

            self.log.info(f"Initialized tracking for {symbol}")

    # ---------- Hook overrides ----------

    def _on_live_activated(
        self, symbol: str, indicator: Indicator, current_bar: int
    ) -> None:
        if self._live_config.on_live_activated_fn:
            self._live_config.on_live_activated_fn(self, symbol, indicator, current_bar)
        elif self._live_config.use_dual_mode:
            indicator.enable_live_mode()
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")
        else:
            self.log.info(f"{symbol} | LIVE TRADING ACTIVATED at bar {current_bar}")

    def _process_signal(
        self, symbol: str, signal, price: float, current_bar: int
    ) -> None:
        if self._live_config.process_signal_fn:
            self._live_config.process_signal_fn(
                self, symbol, signal, price, current_bar
            )
        elif self._live_config.use_dual_mode:
            self._execute_signal(symbol, signal, price)
        else:
            super()._process_signal(symbol, signal, price, current_bar)

    def _pre_signal_hook(
        self,
        symbol: str,
        signal,
        price: float,
        indicator: Indicator,
        current_bar: int,
    ) -> bool:
        if self._live_config.pre_signal_hook_fn:
            return self._live_config.pre_signal_hook_fn(
                self, symbol, signal, price, indicator, current_bar
            )
        return False

    def on_funding_rate(self, funding_rate: FundingRate) -> None:
        """Delegate funding rate events to LiveConfig callback."""
        if self._live_config.on_funding_rate_fn:
            self._live_config.on_funding_rate_fn(self, funding_rate)

    def _format_log_line(
        self,
        symbol: str,
        signal,
        position: PositionState,
        indicator: Indicator,
        current_bar: int,
    ) -> str:
        pos_str = (
            f"{position.side.value}@{position.entry_price:.0f}"
            if position and position.side
            else "FLAT"
        )
        bars_held = (
            current_bar - position.entry_bar if position and position.side else 0
        )
        cooldown_remaining = max(0, self._cooldown_until.get(symbol, 0) - current_bar)
        return (
            f"{symbol} | Bar={current_bar} | "
            f"Signal={signal.value} | Pos={pos_str} | "
            f"Hold={bars_held} | CD={cooldown_remaining}"
        )


def _default_warmup(config) -> int:
    """Compute a default warmup period by scanning config for period-like fields."""
    max_period = 20
    for f in dataclasses.fields(config):
        name = f.name
        val = getattr(config, name)
        if isinstance(val, int) and any(
            kw in name for kw in ("period", "window", "lookback", "slow", "trend")
        ):
            if name.startswith("adx"):
                max_period = max(max_period, val * 2 + 1)
            else:
                max_period = max(max_period, val)
    return max_period + 10
