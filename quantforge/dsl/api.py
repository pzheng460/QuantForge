"""Declarative Strategy API — the core of the simplified strategy framework.

Provides:
- Strategy: base class with auto-registration, signal constants, indicator management
- Param: descriptor for strategy parameters with optimization grid support
- Bar: lightweight data container for OHLCV bar data
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from quantforge.dsl.indicators import Indicator, create_indicator


@dataclasses.dataclass(slots=True)
class Bar:
    """OHLCV bar data passed to on_bar()."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    index: int = 0


class Param:
    """Strategy parameter descriptor with optimization grid support.

    Usage:
        class MyStrategy(Strategy):
            fast_period = Param(12, min=5, max=30, step=2)
            mode = Param("aggressive", choices=["conservative", "aggressive"])

    Access via self.fast_period returns the current value.
    Grid generation via Param.grid() for optimization.
    """

    def __init__(
        self,
        default: Any,
        *,
        min: Optional[float] = None,
        max: Optional[float] = None,
        step: Optional[float] = None,
        choices: Optional[list] = None,
    ):
        self.default = default
        self.min = min
        self.max = max
        self.step = step
        self.choices = choices
        self._attr_name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj._param_values.get(self._attr_name, self.default)

    def __set__(self, obj, value):
        obj._param_values[self._attr_name] = value

    def grid(self) -> list:
        """Generate optimization grid values."""
        if self.choices is not None:
            return list(self.choices)
        if self.min is not None and self.max is not None:
            step = self.step if self.step is not None else 1
            values = []
            v = self.min
            while v <= self.max:
                if isinstance(self.default, int):
                    values.append(int(v))
                else:
                    values.append(round(v, 10))
                v += step
            return values
        return [self.default]


class _StrategyMeta(type):
    """Metaclass that auto-registers Strategy subclasses."""

    def __new__(mcs, name: str, bases: tuple, namespace: dict):
        cls = super().__new__(mcs, name, bases, namespace)
        # Don't register the base Strategy class itself
        if bases and any(b.__name__ == "Strategy" for b in bases):
            from quantforge.dsl.registry import _register

            _register(cls)
        return cls


class Strategy(metaclass=_StrategyMeta):
    """Base class for declarative strategies.

    Subclass and implement setup() + on_bar() to define a strategy.

    Class attributes:
        name: str — unique strategy identifier
        timeframe: str — default bar interval (e.g. "15m", "1h")

    Signal constants:
        HOLD = 0, BUY = 1, SELL = -1, CLOSE = 2
    """

    # Signal constants
    HOLD = 0
    BUY = 1
    SELL = -1
    CLOSE = 2

    # Subclass should override
    name: str = ""
    timeframe: str = "15m"

    def __init__(self, **params):
        self._param_values: dict[str, Any] = {}
        self._indicators: list[Indicator] = []
        self._bar_index: int = 0

        # Set default values from Param descriptors, then apply overrides
        for attr_name, param in self._get_params().items():
            self._param_values[attr_name] = param.default
        for k, v in params.items():
            if k in self._get_params():
                self._param_values[k] = v

        # Initialize indicators via setup()
        self.setup()

    @classmethod
    def _get_params(cls) -> dict[str, Param]:
        """Get all Param descriptors defined on this class."""
        params = {}
        for klass in reversed(cls.__mro__):
            for attr_name, attr_val in vars(klass).items():
                if isinstance(attr_val, Param):
                    params[attr_name] = attr_val
        return params

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        """Generate optimization grid from Param descriptors."""
        return {name: param.grid() for name, param in cls._get_params().items()}

    def add_indicator(self, indicator_type: str, *args) -> Indicator:
        """Create and register an indicator.

        Args:
            indicator_type: "ema", "sma", "rsi", "atr", "adx", "bb", "roc"
            *args: Arguments for the indicator (e.g. period).

        Returns:
            Indicator wrapper with .value, .ready, .crossover(), etc.
        """
        ind = create_indicator(indicator_type, *args)
        self._indicators.append(ind)
        return ind

    @property
    def bar_index(self) -> int:
        """Current bar index (0-based)."""
        return self._bar_index

    def setup(self) -> None:
        """Initialize indicators. Override in subclass."""

    def on_bar(self, bar: Bar) -> int:
        """Called on each bar. Return signal: BUY=1, SELL=-1, CLOSE=2, HOLD=0.

        Override in subclass.
        """
        return self.HOLD

    def _process_bar(self, bar: Bar) -> int:
        """Internal: update all indicators, then call on_bar."""
        bar.index = self._bar_index
        for ind in self._indicators:
            ind._update(bar)
        signal = self.on_bar(bar)
        self._bar_index += 1
        return signal

    def reset(self) -> None:
        """Reset strategy state: indicators and bar index."""
        self._bar_index = 0
        for ind in self._indicators:
            ind.reset()
        # Re-run setup to reinitialize any strategy-level state
        self._indicators.clear()
        self.setup()
