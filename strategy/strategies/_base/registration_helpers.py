"""
Registration Helper Factories.

Auto-generate registration helper functions (split_params, filter_config_factory,
mesa_dict_to_config, export_config) from strategy metadata, eliminating
per-strategy boilerplate.
"""

import dataclasses
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple, Type

from strategy.backtest.config import StrategyConfig


def make_split_params_fn(config_cls: Type) -> Callable:
    """Generate a split_params function from a config dataclass.

    The returned function splits a mixed params dict into
    (config_kwargs, filter_kwargs) based on which field names
    belong to config_cls.
    """
    config_fields = {f.name for f in dataclasses.fields(config_cls)}

    def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
        if not params:
            return {}, {}
        config_kw = {k: v for k, v in params.items() if k in config_fields}
        filter_kw = {k: v for k, v in params.items() if k not in config_fields}
        return config_kw, filter_kw

    return _split_params


def make_filter_config_factory(
    filter_config_cls: Type,
    min_hold_formula: Optional[Callable] = None,
) -> Callable:
    """Generate a filter_config_factory for heatmap scanning.

    Args:
        filter_config_cls: The filter config dataclass to instantiate.
        min_hold_formula: Optional callable(xv, yv, params) -> int.
            Default: max(2, int(yv) // 5).
    """

    def factory(xv, yv, params):
        if min_hold_formula:
            min_hold = min_hold_formula(xv, yv, params)
        else:
            min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 5)))
        cooldown = max(1, min_hold // 2)

        kwargs: Dict[str, Any] = {
            "min_holding_bars": min_hold,
            "cooldown_bars": cooldown,
        }

        # Add any extra fields that the filter config supports
        for f in dataclasses.fields(filter_config_cls):
            if f.name in kwargs:
                continue
            if f.name == "signal_confirmation":
                kwargs["signal_confirmation"] = int(
                    params.get("signal_confirmation", 1)
                )
            elif f.name in params:
                kwargs[f.name] = params[f.name]

        return filter_config_cls(**kwargs)

    return factory


def _get_field_default(f: dataclasses.Field) -> Any:
    """Get the default value for a dataclass field, or None if no default."""
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return None


def _cast_value(value: Any, target_type: type) -> Any:
    """Cast a value to the target type."""
    if target_type is bool:
        return bool(value)
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is str:
        return str(value) if value is not None else None
    if target_type is type(None):
        # Optional field with None default — pass through unchanged
        return value if value else None
    return value


def make_mesa_dict_to_config(
    config_cls: Type,
    filter_config_cls: Type,
    x_param_name: str,
    y_param_name: str,
    x_label: str = None,
    y_label: str = None,
    min_hold_from_mesa: Optional[Callable] = None,
) -> Callable:
    """Generate a mesa_dict_to_config function by introspecting config fields.

    Args:
        config_cls: Strategy config dataclass (e.g. EMAConfig).
        filter_config_cls: Trade filter config dataclass.
        x_param_name: Name of the x-axis parameter in config.
        y_param_name: Name of the y-axis parameter in config.
        x_label: Display label for x param (defaults to x_param_name).
        y_label: Display label for y param (defaults to y_param_name).
        min_hold_from_mesa: Optional callable(mesa, extra) -> int for
            computing min_holding_bars from mesa data. Default uses
            int(extra.get("min_holding_bars", 4)).
    """
    x_label = x_label or x_param_name.replace("_", " ").title()
    y_label = y_label or y_param_name.replace("_", " ").title()

    # Pre-compute field metadata
    config_field_map = {f.name: f for f in dataclasses.fields(config_cls)}
    filter_field_map = {f.name: f for f in dataclasses.fields(filter_config_cls)}

    # Determine type casters for x and y
    x_field = config_field_map.get(x_param_name)
    y_field = config_field_map.get(y_param_name)

    def _cast_xy(val, field_info):
        if field_info is None:
            return val
        default = _get_field_default(field_info)
        if default is not None:
            return _cast_value(val, type(default))
        return val

    def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
        extra = mesa.get("extra_params", {})

        # Extract x, y center values
        x_val = _cast_xy(
            mesa.get("center_x", mesa.get(f"center_{x_param_name}", 0)),
            x_field,
        )
        y_val = _cast_xy(
            mesa.get("center_y", mesa.get(f"center_{y_param_name}", 0)),
            y_field,
        )

        # Build config kwargs by introspecting all fields
        config_kwargs: Dict[str, Any] = {}
        for fname, finfo in config_field_map.items():
            if fname == "symbols" or fname == "timeframe":
                continue  # Use defaults
            if fname == x_param_name:
                config_kwargs[fname] = x_val
            elif fname == y_param_name:
                config_kwargs[fname] = y_val
            elif fname in extra:
                default = _get_field_default(finfo)
                if default is not None:
                    config_kwargs[fname] = _cast_value(extra[fname], type(default))
                else:
                    config_kwargs[fname] = extra[fname]

        config = config_cls(**config_kwargs)

        # Build filter config
        if min_hold_from_mesa:
            min_hold = min_hold_from_mesa(mesa, extra)
        else:
            min_hold = int(extra.get("min_holding_bars", 4))
        cooldown = max(1, min_hold // 2)
        filter_kwargs: Dict[str, Any] = {
            "min_holding_bars": min_hold,
            "cooldown_bars": cooldown,
        }
        for fname, finfo in filter_field_map.items():
            if fname in filter_kwargs:
                continue
            if fname in extra:
                default = _get_field_default(finfo)
                if default is not None:
                    filter_kwargs[fname] = _cast_value(extra[fname], type(default))
                else:
                    filter_kwargs[fname] = extra[fname]
        filter_config = filter_config_cls(**filter_kwargs)

        # Standard metadata
        freq_label = mesa.get("frequency_label", "")
        x_range = mesa.get("x_range", mesa.get(f"{x_param_name}_range", [0, 0]))
        y_range = mesa.get("y_range", mesa.get(f"{y_param_name}_range", [0, 0]))

        return StrategyConfig(
            name=f"Mesa #{index} ({freq_label})",
            description=(
                f"Auto-detected Mesa region. "
                f"{x_label} [{x_range[0]:.0f}, {x_range[1]:.0f}], "
                f"{y_label} [{y_range[0]:.0f}, {y_range[1]:.0f}]"
            ),
            strategy_config=config,
            filter_config=filter_config,
            recommended=(index == 0),
            mesa_index=index,
            frequency_label=freq_label,
            avg_sharpe=mesa.get("avg_sharpe", 0),
            stability=mesa.get("stability", 0),
            notes=(
                f"Avg return: {mesa.get('avg_return_pct', 0):+.1f}%/yr, "
                f"MaxDD: {mesa.get('avg_max_dd_pct', 0):.1f}%, "
                f"Trades: {mesa.get('avg_trades_yr', 0):.0f}/yr"
            ),
        )

    return _mesa_dict_to_config


def _format_field(fname: str, val: Any, default: Any) -> str:
    """Format a single field for Python code generation.

    Returns empty string if the field can't be formatted (skip it).
    """
    # bool must be checked before int (bool is subclass of int)
    if isinstance(default, bool):
        return f"    {fname}={bool(val)},"
    if isinstance(default, float) or isinstance(val, float):
        return f"    {fname}={float(val)},"
    if isinstance(default, int) or isinstance(val, int):
        return f"    {fname}={int(val)},"
    if isinstance(default, str):
        return f"    {fname}={val!r},"
    # Handle None default (Optional fields)
    if default is None:
        return f"    {fname}={val!r},"
    return ""


def make_export_config(
    strategy_name: str,
    config_cls: Type,
    filter_config_cls: Type,
    config_import_path: str,
    filter_import_path: str,
) -> Callable:
    """Generate an export_config function by introspecting config fields.

    Args:
        strategy_name: Strategy module name (e.g. "ema_crossover").
        config_cls: Strategy config dataclass.
        filter_config_cls: Trade filter config dataclass.
        config_import_path: Import path for config class
            (e.g. "strategy.strategies.ema_crossover.core").
        filter_import_path: Import path for filter config class
            (e.g. "strategy.strategies._base.signal_generator").
    """
    config_name = config_cls.__name__
    filter_name = filter_config_cls.__name__

    # Pre-compute field info
    config_fields = [
        (f.name, _get_field_default(f))
        for f in dataclasses.fields(config_cls)
        if f.name not in ("timeframe",)
    ]
    filter_fields = [
        (f.name, _get_field_default(f))
        for f in dataclasses.fields(filter_config_cls)
        if f.name not in ("min_holding_bars", "cooldown_bars")
    ]

    def _export_config(
        params: Dict, metrics: Dict, period: str = None, profile=None
    ) -> str:
        min_hold = int(params.get("min_holding_bars", 4))
        cooldown = max(1, min_hold // 2)
        suffix = profile.nexus_symbol_suffix if profile else ".BITGET"

        # Build config lines
        config_lines = []
        for fname, default in config_fields:
            if fname == "symbols":
                config_lines.append(f'    symbols=["BTCUSDT-PERP{suffix}"],')
                continue
            if isinstance(default, list):
                continue  # Skip list fields
            val = params.get(fname, default)
            config_lines.append(_format_field(fname, val, default))

        # Remove None entries (fields that couldn't be formatted)
        config_lines = [line for line in config_lines if line]

        # Build filter lines
        filter_lines = [
            f"    min_holding_bars={min_hold},",
            f"    cooldown_bars={cooldown},",
        ]
        for fname, default in filter_fields:
            val = params.get(fname, default)
            line = _format_field(fname, val, default)
            if line:
                filter_lines.append(line)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return_pct = metrics.get("total_return_pct", 0)
        sharpe = metrics.get("sharpe_ratio", 0)

        return f"""
# =============================================================================
# OPTIMIZED CONFIG (Generated: {now})
# Period: {period or "N/A"}
# Performance: {return_pct:.1f}% return, {sharpe:.2f} Sharpe
# =============================================================================

from {config_import_path} import {config_name}
from {filter_import_path} import {filter_name}

OPTIMIZED_CONFIG = {config_name}(
{chr(10).join(config_lines)}
)

OPTIMIZED_FILTER = {filter_name}(
{chr(10).join(filter_lines)}
)
"""

    return _export_config
