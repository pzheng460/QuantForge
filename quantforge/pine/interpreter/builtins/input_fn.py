"""Input builtins: input.int, input.float, input.bool, input.string, input.source."""

from __future__ import annotations


def input_int(ctx, defval: int = 0, title: str = "", **kwargs) -> int:
    """Return integer input value, checking context overrides first."""
    key = title or f"input_int_{defval}"
    return int(ctx.inputs.get(key, defval))


def input_float(ctx, defval: float = 0.0, title: str = "", **kwargs) -> float:
    key = title or f"input_float_{defval}"
    return float(ctx.inputs.get(key, defval))


def input_bool(ctx, defval: bool = False, title: str = "", **kwargs) -> bool:
    key = title or f"input_bool_{defval}"
    return bool(ctx.inputs.get(key, defval))


def input_string(ctx, defval: str = "", title: str = "", **kwargs) -> str:
    key = title or f"input_string_{defval}"
    return str(ctx.inputs.get(key, defval))


def input_source(ctx, defval=None, title: str = "", **kwargs):
    """Source input – returns a series. Default is close."""
    key = title or "Source"
    override = ctx.inputs.get(key)
    if override is not None:
        if isinstance(override, str):
            return ctx.get_series(override)
        return override
    if defval is not None:
        if isinstance(defval, str):
            return ctx.get_series(defval)
        return defval
    return ctx.get_series("close")
