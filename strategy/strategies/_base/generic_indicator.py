"""
Generic indicator wrapper for any SignalCore class.

Wraps any signal core from strategy/indicators/ into a NexusTrader Indicator,
handling bar confirmation, warmup, and signal generation generically.

Usage:
    indicator = GenericIndicator(
        core_cls=EMASignalCore,
        config=ema_config,
        update_columns=("close",),
        warmup_period_bars=30,
        kline_interval=KlineInterval.MINUTE_15,
        filter_params={"min_holding_bars": 4, "cooldown_bars": 2, "signal_confirmation": 1},
    )
"""

import inspect
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple, Type

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


# Map int signal constants (from all SignalCore classes) to Signal enum
_SIGNAL_MAP = {0: Signal.HOLD, 1: Signal.BUY, -1: Signal.SELL, 2: Signal.CLOSE}


def _get_kline_kwargs(kline: Kline, param_names: Tuple[str, ...]) -> Dict[str, Any]:
    """Build kwargs dict from a kline for the given parameter names."""
    kline_map = {
        "close": float(kline.close),
        "price": float(kline.close),
        "high": float(kline.high),
        "low": float(kline.low),
        "volume": float(kline.volume),
    }
    return {name: kline_map[name] for name in param_names if name in kline_map}


def _introspect_params(method) -> Tuple[str, ...]:
    """Return the parameter names of a method (excluding 'self')."""
    sig = inspect.signature(method)
    return tuple(p for p in sig.parameters if p != "self")


class GenericIndicator(Indicator):
    """Generic indicator wrapper that works with any SignalCore class.

    Handles:
    - Bar confirmation via kline.start timestamp change
    - Warmup via confirmed bar counting
    - Signal generation via update_indicators_only + get_raw_signal
    - Optional dual-mode (live mode uses core.update() for position management)
    - Optional should_stop_loss (percentage-based)
    """

    def __init__(
        self,
        core_cls: Type,
        config: Any,
        *,
        update_columns: Tuple[str, ...] = ("close",),
        warmup_period_bars: int = 50,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
        filter_params: Optional[Dict[str, Any]] = None,
        use_dual_mode: bool = False,
        pre_update_hook: Optional[Callable] = None,
    ):
        """
        Args:
            core_cls: SignalCore class (e.g. EMASignalCore, BBSignalCore).
            config: Strategy config dataclass instance.
            update_columns: Columns to pass to update_indicators_only/update.
            warmup_period_bars: Number of confirmed bars needed before trading.
            kline_interval: Kline interval for this indicator.
            filter_params: Dict of filter params to pass to core constructor
                (min_holding_bars, cooldown_bars, signal_confirmation, etc.)
                Only used when use_dual_mode=True.
            use_dual_mode: If True, supports enable_live_mode() which switches
                from update_indicators_only to core.update().
            pre_update_hook: Optional callable(core, kline) called before
                update_indicators_only to inject extra kwargs (e.g. day for VWAP).
        """
        self._config = config
        self._warmup_bars = warmup_period_bars
        self._use_dual_mode = use_dual_mode
        self._pre_update_hook = pre_update_hook

        super().__init__(
            params={"strategy": core_cls.__name__},
            name=core_cls.__name__,
            warmup_period=None,
            kline_interval=kline_interval,
        )

        # Build core constructor kwargs
        core_kwargs: Dict[str, Any] = {}
        # Introspect core __init__ to see what it accepts
        core_init_params = _introspect_params(core_cls.__init__)
        if "config" in core_init_params:
            core_kwargs["config"] = config
        # Forward filter params if the core accepts them
        if filter_params and use_dual_mode:
            for key, value in filter_params.items():
                if key in core_init_params:
                    core_kwargs[key] = value

        self._core = core_cls(**core_kwargs)

        # Introspect method signatures for later use
        self._update_params = _introspect_params(self._core.update_indicators_only)
        self._update_method_params = _introspect_params(self._core.update)

        # Resolve signal method: get_raw_signal → get_signal → None
        if hasattr(self._core, "get_raw_signal"):
            self._signal_method = self._core.get_raw_signal
        elif hasattr(self._core, "get_signal"):
            self._signal_method = self._core.get_signal
        else:
            self._signal_method = None
        self._signal_params = (
            _introspect_params(self._signal_method) if self._signal_method else ()
        )

        # Update columns (for building kwargs from kline)
        self._update_columns = update_columns

        # State
        self._confirmed_bar_count: int = 0
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None
        self._live_mode: bool = False

    # ---------- Kline handling ----------

    def handle_kline(self, kline: Kline) -> None:
        """Process a kline. Bar confirmation via timestamp change."""
        bar_start = int(kline.start)

        if not hasattr(self, "_current_bar_start"):
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
            return

        if bar_start != self._current_bar_start:
            confirmed_kline = self._current_bar_kline
            self._confirmed_bar_count += 1
            self._process_kline_data(confirmed_kline)

            self._current_bar_start = bar_start
            self._current_bar_kline = kline
        else:
            self._current_bar_kline = kline

    @property
    def is_warmed_up(self) -> bool:
        """Check if enough confirmed bars have been processed."""
        return self._confirmed_bar_count >= self._warmup_bars

    def enable_live_mode(self) -> None:
        """Switch to live mode: core.update() handles position management."""
        self._live_mode = True

    def _process_kline_data(self, kline: Kline) -> None:
        """Process a confirmed bar through the signal core."""
        price = float(kline.close)
        self._last_price = price

        # Pre-update hook (e.g. for VWAP day boundary detection)
        extra_kwargs: Dict[str, Any] = {}
        if self._pre_update_hook:
            hook_result = self._pre_update_hook(self._core, kline)
            if isinstance(hook_result, dict):
                extra_kwargs = hook_result

        if self._live_mode and self._use_dual_mode:
            # Live mode: core.update() returns signal and manages position state
            update_args = _get_kline_kwargs(kline, self._update_method_params)
            # Only pass extra_kwargs whose keys match the method's parameters
            for k, v in extra_kwargs.items():
                if k in self._update_method_params:
                    update_args[k] = v
            raw = self._core.update(**update_args)
        else:
            # Warmup mode: update indicators only, compute raw signal separately
            update_args = _get_kline_kwargs(kline, self._update_params)
            for k, v in extra_kwargs.items():
                if k in self._update_params:
                    update_args[k] = v
            self._core.update_indicators_only(**update_args)

            if self._signal_method is not None:
                signal_args = _get_kline_kwargs(kline, self._signal_params)
                for k, v in extra_kwargs.items():
                    if k in self._signal_params:
                        signal_args[k] = v
                raw = self._signal_method(**signal_args)
            else:
                raw = 0  # HOLD — no signal method (e.g. GridSignalCore)

        self._signal = _SIGNAL_MAP.get(raw, Signal.HOLD)

    # ---------- Unused handlers ----------

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # ---------- Properties ----------

    @property
    def core(self) -> Any:
        """Access the underlying SignalCore instance."""
        return self._core

    @property
    def value(self) -> dict:
        return {"signal": self._signal.value}

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Percentage-based stop loss check."""
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        stop_loss_pct = getattr(self._config, "stop_loss_pct", 0.03)
        return pnl_pct < -stop_loss_pct

    def reset(self) -> None:
        self._core.reset()
        self._signal = Signal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
