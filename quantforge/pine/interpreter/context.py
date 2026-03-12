"""Execution context for Pine Script interpreter.

Manages variables, series, OHLCV data, and the bar-by-bar execution environment.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quantforge.pine.interpreter.series import PineSeries


@dataclass
class BarData:
    """OHLCV data for a single bar."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    time: int = 0  # unix timestamp


class ExecutionContext:
    """Manages all state for a Pine Script execution session."""

    def __init__(self, bars: list[BarData] | None = None) -> None:
        self.bars: list[BarData] = bars or []
        self.bar_index: int = -1  # current bar index

        # Variable storage
        self._variables: dict[str, object] = {}
        # "var" declared variables (persist across bars, initialised once)
        self._var_declared: set[str] = set()
        # Series storage
        self._series: dict[str, PineSeries] = {}

        # Built-in OHLCV series
        self._ohlcv_names = ("open", "high", "low", "close", "volume", "time")
        for name in self._ohlcv_names:
            self._series[name] = PineSeries(name)

        # bar_index series
        self._series["bar_index"] = PineSeries("bar_index")

        # Function definitions (user-defined)
        self.functions: dict[str, object] = {}

        # Strategy state (set by strategy builtins)
        self.strategy_context: object | None = None

        # Input defaults
        self.inputs: dict[str, object] = {}

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> ExecutionContext:
        """Create context from a pandas DataFrame with OHLCV columns."""
        bars = []
        for _, row in df.iterrows():
            bars.append(
                BarData(
                    open=float(row.get("open", row.get("Open", 0))),
                    high=float(row.get("high", row.get("High", 0))),
                    low=float(row.get("low", row.get("Low", 0))),
                    close=float(row.get("close", row.get("Close", 0))),
                    volume=float(row.get("volume", row.get("Volume", 0))),
                    time=int(row.get("time", row.get("timestamp", 0))),
                )
            )
        return cls(bars=bars)

    @classmethod
    def from_arrays(
        cls,
        open: list[float],
        high: list[float],
        low: list[float],
        close: list[float],
        volume: list[float] | None = None,
    ) -> ExecutionContext:
        """Create context from separate OHLCV arrays."""
        n = len(close)
        vol = volume or [0.0] * n
        bars = [
            BarData(
                open=open[i], high=high[i], low=low[i], close=close[i], volume=vol[i]
            )
            for i in range(n)
        ]
        return cls(bars=bars)

    # --- bar lifecycle ---

    def advance_bar(self) -> bool:
        """Move to the next bar. Returns False if no more bars."""
        self.bar_index += 1
        if self.bar_index >= len(self.bars):
            return False

        bar = self.bars[self.bar_index]
        self._series["open"].append(bar.open)
        self._series["high"].append(bar.high)
        self._series["low"].append(bar.low)
        self._series["close"].append(bar.close)
        self._series["volume"].append(bar.volume)
        self._series["time"].append(bar.time)
        self._series["bar_index"].append(self.bar_index)
        return True

    @property
    def current_bar(self) -> BarData | None:
        if 0 <= self.bar_index < len(self.bars):
            return self.bars[self.bar_index]
        return None

    # --- variable access ---

    def get_var(self, name: str):
        """Get a variable or series current value."""
        if name in self._series:
            return self._series[name].current
        return self._variables.get(name)

    def set_var(self, name: str, value, declaration: str | None = None) -> None:
        """Set a variable. If declaration='var', only set on first bar."""
        if declaration == "var":
            if name not in self._var_declared:
                self._var_declared.add(name)
                self._variables[name] = value
            # On subsequent bars, don't reinitialise
            return
        self._variables[name] = value

    def has_var(self, name: str) -> bool:
        return name in self._variables or name in self._series

    # --- series access ---

    def get_series(self, name: str) -> PineSeries:
        """Get or create a named series."""
        if name not in self._series:
            self._series[name] = PineSeries(name)
        return self._series[name]

    def set_series_value(self, name: str, value) -> None:
        """Append a value to a named series on the current bar."""
        series = self.get_series(name)
        if len(series) <= self.bar_index:
            series.append(value)
        else:
            series.set_current(value)

    def get_history(self, name: str, offset: int):
        """Get historical value: name[offset]."""
        if name in self._series:
            return self._series[name][offset]
        return None

    @property
    def total_bars(self) -> int:
        return len(self.bars)
