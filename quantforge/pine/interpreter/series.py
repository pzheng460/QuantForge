"""PineSeries — stores historical bar values with indexing support."""

from __future__ import annotations

import math
from collections import deque


class PineSeries:
    """Series type matching Pine Script semantics.

    series[0] = current bar value
    series[1] = previous bar value
    series[n] = n bars ago
    """

    def __init__(self, maxlen: int = 5000):
        self._data: deque[float | None] = deque(maxlen=maxlen)

    def push(self, value: float | None) -> None:
        """Add a new bar value (called once per bar)."""
        self._data.appendleft(value)

    def __getitem__(self, offset: int) -> float | None:
        """Get value at offset bars ago. 0 = current, 1 = previous."""
        if offset < 0:
            return None
        if offset >= len(self._data):
            return None
        return self._data[offset]

    def __setitem__(self, offset: int, value: float | None) -> None:
        """Set value at offset (only offset=0 is typical)."""
        if offset == 0 and len(self._data) > 0:
            self._data[0] = value
        elif offset == 0:
            self._data.appendleft(value)

    @property
    def current(self) -> float | None:
        return self[0]

    @property
    def previous(self) -> float | None:
        return self[1]

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        vals = [self._data[i] for i in range(min(5, len(self._data)))]
        return f"PineSeries({vals}{'...' if len(self._data) > 5 else ''})"

    def is_na(self, offset: int = 0) -> bool:
        v = self[offset]
        if v is None:
            return True
        if isinstance(v, float) and math.isnan(v):
            return True
        return False

    def to_list(self, n: int | None = None) -> list[float | None]:
        """Return values as list, most recent first."""
        if n is None:
            return list(self._data)
        return [self._data[i] for i in range(min(n, len(self._data)))]
