"""input.* built-in functions for Pine Script.

Implements: input.int, input.float, input.bool, input.string, input.source.
"""

from __future__ import annotations


class InputManager:
    """Manages Pine Script input() parameters.

    Users can override input defaults via the `overrides` dict passed at construction.
    """

    def __init__(self, overrides: dict[str, object] | None = None):
        self._overrides = overrides or {}
        self._inputs: dict[str, object] = {}

    def input_int(
        self,
        defval: int = 0,
        title: str = "",
        minval: int | None = None,
        maxval: int | None = None,
        step: int = 1,
        tooltip: str = "",
        group: str = "",
        inline: str = "",
    ) -> int:
        key = title or f"_input_{len(self._inputs)}"
        val = self._overrides.get(key, defval)
        val = int(val)
        if minval is not None:
            val = max(val, minval)
        if maxval is not None:
            val = min(val, maxval)
        self._inputs[key] = val
        return val

    def input_float(
        self,
        defval: float = 0.0,
        title: str = "",
        minval: float | None = None,
        maxval: float | None = None,
        step: float = 1.0,
        tooltip: str = "",
        group: str = "",
        inline: str = "",
    ) -> float:
        key = title or f"_input_{len(self._inputs)}"
        val = self._overrides.get(key, defval)
        val = float(val)
        if minval is not None:
            val = max(val, minval)
        if maxval is not None:
            val = min(val, maxval)
        self._inputs[key] = val
        return val

    def input_bool(
        self,
        defval: bool = False,
        title: str = "",
        tooltip: str = "",
        group: str = "",
        inline: str = "",
    ) -> bool:
        key = title or f"_input_{len(self._inputs)}"
        val = self._overrides.get(key, defval)
        self._inputs[key] = bool(val)
        return bool(val)

    def input_string(
        self,
        defval: str = "",
        title: str = "",
        options: list[str] | None = None,
        tooltip: str = "",
        group: str = "",
        inline: str = "",
    ) -> str:
        key = title or f"_input_{len(self._inputs)}"
        val = self._overrides.get(key, defval)
        val = str(val)
        if options and val not in options:
            val = defval
        self._inputs[key] = val
        return val

    def input_source(
        self,
        defval: str = "close",
        title: str = "",
        tooltip: str = "",
        group: str = "",
        inline: str = "",
    ) -> str:
        """input.source() — returns the name of the source series.

        The runtime resolves this string to the actual series data.
        """
        key = title or f"_input_{len(self._inputs)}"
        val = self._overrides.get(key, defval)
        val = str(val)
        valid = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"}
        if val not in valid:
            val = defval
        self._inputs[key] = val
        return val

    @property
    def all_inputs(self) -> dict[str, object]:
        return dict(self._inputs)
