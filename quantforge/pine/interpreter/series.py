"""PineSeries – time-series type for Pine Script interpreter."""

from __future__ import annotations

import math


class PineSeries:
    """A time series that grows bar-by-bar, supporting history-reference via [].

    Internally stores a list of values.  Index 0 is the current bar,
    [1] is the previous bar, etc.
    """

    __slots__ = ("_data", "_name")

    def __init__(self, name: str = "") -> None:
        self._data: list = []
        self._name = name

    def append(self, value) -> None:
        self._data.append(value)

    def set_current(self, value) -> None:
        if self._data:
            self._data[-1] = value
        else:
            self._data.append(value)

    @property
    def current(self):
        return self._data[-1] if self._data else None

    def __getitem__(self, offset: int):
        if offset < 0:
            return None
        idx = len(self._data) - 1 - offset
        if idx < 0:
            return None
        return self._data[idx]

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        name = self._name or "PineSeries"
        return f"{name}({self.current}, len={len(self._data)})"

    def to_list(self) -> list:
        return list(self._data)


def is_na(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def nz(value, replacement=0):
    return replacement if is_na(value) else value
