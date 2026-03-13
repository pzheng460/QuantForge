"""Indicator wrappers with crossover/crossunder and history tracking.

Wraps the existing streaming indicator classes from
strategy/strategies/_base/streaming.py, adding:
- .ready property
- .crossover(other) / .crossunder(other)
- [n] lookback indexing
- Automatic history buffer
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from strategy.strategies._base.streaming import (
    StreamingADX,
    StreamingATR,
    StreamingBB,
    StreamingEMA,
    StreamingROC,
    StreamingRSI,
    StreamingSMA,
)

# Map indicator type names to (StreamingClass, update_pattern)
# update_pattern: "price" = update(price), "hlc" = update(high, low, close)
_INDICATOR_MAP: dict[str, tuple[type, str]] = {
    "ema": (StreamingEMA, "price"),
    "sma": (StreamingSMA, "price"),
    "rsi": (StreamingRSI, "price"),
    "roc": (StreamingROC, "price"),
    "atr": (StreamingATR, "hlc"),
    "adx": (StreamingADX, "hlc"),
    "bb": (StreamingBB, "price"),
}

# Maximum history buffer size
_MAX_HISTORY = 256


class Indicator:
    """Wrapper around streaming indicators with crossover detection and history.

    Provides a user-friendly API:
        ind.value       — current value (None if not ready)
        ind.ready       — True when indicator has enough data
        ind[0]          — current value (same as .value)
        ind[1]          — previous value
        ind.crossover(other)   — True if crossed above other indicator
        ind.crossunder(other)  — True if crossed below other indicator
    """

    def __init__(self, inner, update_pattern: str = "price"):
        self._inner = inner
        self._update_pattern = update_pattern
        self._history: deque[Optional[float]] = deque(maxlen=_MAX_HISTORY)
        self._is_bb = isinstance(inner, StreamingBB)

        # For BB, expose sub-values
        if self._is_bb:
            self._upper_history: deque[Optional[float]] = deque(maxlen=_MAX_HISTORY)
            self._lower_history: deque[Optional[float]] = deque(maxlen=_MAX_HISTORY)

    def _update(self, bar) -> Optional[float]:
        """Update the indicator with bar data. Called by Strategy._process_bar."""
        if self._update_pattern == "hlc":
            result = self._inner.update(bar.high, bar.low, bar.close)
        else:
            result = self._inner.update(bar.close)

        if self._is_bb:
            sma, upper, lower = (
                result if result != (None, None, None) else (None, None, None)
            )
            self._history.appendleft(sma)
            self._upper_history.appendleft(upper)
            self._lower_history.appendleft(lower)
            return sma
        else:
            self._history.appendleft(result)
            return result

    @property
    def value(self) -> Optional[float]:
        """Current indicator value."""
        if not self._history:
            return None
        return self._history[0]

    @property
    def ready(self) -> bool:
        """True when the indicator has produced at least one non-None value."""
        return self.value is not None

    @property
    def upper(self) -> Optional[float]:
        """Upper band (Bollinger Bands only)."""
        if self._is_bb and self._upper_history:
            return self._upper_history[0]
        return None

    @property
    def lower(self) -> Optional[float]:
        """Lower band (Bollinger Bands only)."""
        if self._is_bb and self._lower_history:
            return self._lower_history[0]
        return None

    def __getitem__(self, n: int) -> Optional[float]:
        """Lookback: ind[0] = current, ind[1] = previous, etc."""
        if n < 0 or n >= len(self._history):
            return None
        return self._history[n]

    def crossover(self, other: Indicator) -> bool:
        """True if this indicator crossed ABOVE `other` on the current bar.

        Requires at least 2 values in history for both indicators.
        """
        if len(self._history) < 2 or len(other._history) < 2:
            return False
        curr_self = self._history[0]
        prev_self = self._history[1]
        curr_other = other._history[0]
        prev_other = other._history[1]
        if any(v is None for v in (curr_self, prev_self, curr_other, prev_other)):
            return False
        return prev_self <= prev_other and curr_self > curr_other

    def crossunder(self, other: Indicator) -> bool:
        """True if this indicator crossed BELOW `other` on the current bar.

        Requires at least 2 values in history for both indicators.
        """
        if len(self._history) < 2 or len(other._history) < 2:
            return False
        curr_self = self._history[0]
        prev_self = self._history[1]
        curr_other = other._history[0]
        prev_other = other._history[1]
        if any(v is None for v in (curr_self, prev_self, curr_other, prev_other)):
            return False
        return prev_self >= prev_other and curr_self < curr_other

    def crossed_above(self, level: float) -> bool:
        """True if value crossed above a fixed level."""
        if len(self._history) < 2:
            return False
        curr = self._history[0]
        prev = self._history[1]
        if curr is None or prev is None:
            return False
        return prev <= level and curr > level

    def crossed_below(self, level: float) -> bool:
        """True if value crossed below a fixed level."""
        if len(self._history) < 2:
            return False
        curr = self._history[0]
        prev = self._history[1]
        if curr is None or prev is None:
            return False
        return prev >= level and curr < level

    def reset(self) -> None:
        """Reset indicator state."""
        self._inner.reset()
        self._history.clear()
        if self._is_bb:
            self._upper_history.clear()
            self._lower_history.clear()


def create_indicator(name: str, *args) -> Indicator:
    """Create an Indicator by name.

    Args:
        name: Indicator type ("ema", "sma", "rsi", "atr", "adx", "bb", "roc").
        *args: Arguments passed to the streaming indicator constructor.

    Returns:
        Indicator wrapper instance.
    """
    key = name.lower()
    if key not in _INDICATOR_MAP:
        raise ValueError(
            f"Unknown indicator '{name}'. "
            f"Available: {', '.join(sorted(_INDICATOR_MAP))}"
        )
    cls, pattern = _INDICATOR_MAP[key]
    inner = cls(*args)
    return Indicator(inner, update_pattern=pattern)
