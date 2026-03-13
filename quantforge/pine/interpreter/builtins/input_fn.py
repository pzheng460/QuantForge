"""Input builtins: input.int, input.float, input.bool, input.string, input.source.

Override lookup order: var_name → title → fallback key → defval.
The optimizer passes var_name keys; legacy callers may use title keys.
"""

from __future__ import annotations


def _lookup(ctx, var_name: str, title: str, fallback_key: str, defval):
    """Look up an override value trying var_name, title, then fallback."""
    if var_name and var_name in ctx.inputs:
        return ctx.inputs[var_name]
    if title and title in ctx.inputs:
        return ctx.inputs[title]
    if fallback_key in ctx.inputs:
        return ctx.inputs[fallback_key]
    return defval


def input_int(ctx, defval: int = 0, title: str = "", **kwargs) -> int:
    """Return integer input value, checking context overrides first."""
    var_name = getattr(ctx, '_current_assign_target', '') or ''
    fallback = title or f"input_int_{defval}"
    return int(_lookup(ctx, var_name, title, fallback, defval))


def input_float(ctx, defval: float = 0.0, title: str = "", **kwargs) -> float:
    var_name = getattr(ctx, '_current_assign_target', '') or ''
    fallback = title or f"input_float_{defval}"
    return float(_lookup(ctx, var_name, title, fallback, defval))


def input_bool(ctx, defval: bool = False, title: str = "", **kwargs) -> bool:
    var_name = getattr(ctx, '_current_assign_target', '') or ''
    fallback = title or f"input_bool_{defval}"
    return bool(_lookup(ctx, var_name, title, fallback, defval))


def input_string(ctx, defval: str = "", title: str = "", **kwargs) -> str:
    var_name = getattr(ctx, '_current_assign_target', '') or ''
    fallback = title or f"input_string_{defval}"
    return str(_lookup(ctx, var_name, title, fallback, defval))


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
