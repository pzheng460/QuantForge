"""Pine Script parameter optimizer — grid search over input.* declarations.

Extracts ``input.int`` / ``input.float`` parameters from the Pine AST,
generates a parameter grid from ``minval`` / ``maxval`` / ``step`` kwargs
(or auto-generated defaults), runs the interpreter for each combination,
and ranks results by Sharpe ratio or a user-specified metric.

Usage::

    from quantforge.pine.optimize import extract_pine_inputs, generate_grid, run_optimization
    from quantforge.pine.parser.parser import parse

    ast = parse(open("my_strategy.pine").read())
    inputs = extract_pine_inputs(ast)
    grid = generate_grid(inputs)
    results = run_optimization(ast, bars, grid, warmup_count=0)
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.parser.ast_nodes import (
    Assignment,
    FunctionCall,
    Identifier,
    MemberAccess,
    NumberLiteral,
    Script,
    StringLiteral,
)


@dataclass
class InputParam:
    """A single Pine input.* parameter extracted from the AST."""

    var_name: str  # variable name in Pine script (e.g. "fast_len")
    title: str  # title kwarg or auto-generated key
    input_type: str  # "int" or "float"
    defval: float
    minval: float | None = None
    maxval: float | None = None
    step: float | None = None


@dataclass
class OptResult:
    """Result of a single optimization run."""

    params: dict[str, float]
    net_profit: float = 0.0
    return_pct: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    equity_curve: list[float] = field(default_factory=list)


def _eval_literal(node) -> float | str | None:
    """Extract a literal value from an AST node."""
    if isinstance(node, NumberLiteral):
        return node.value
    if isinstance(node, StringLiteral):
        return node.value
    return None


def _resolve_func_name(func_node) -> str:
    """Resolve a function call name like input.int from the AST."""
    if isinstance(func_node, Identifier):
        return func_node.name
    if isinstance(func_node, MemberAccess):
        obj_name = _resolve_func_name(func_node.obj)
        return f"{obj_name}.{func_node.member}" if obj_name else func_node.member
    return ""


def extract_pine_inputs(ast: Script) -> list[InputParam]:
    """Extract all ``input.int`` / ``input.float`` declarations from a Pine AST.

    Walks the AST body looking for Assignment nodes whose value is a
    FunctionCall to ``input.int`` or ``input.float``.
    """
    inputs: list[InputParam] = []

    for stmt in ast.body:
        if not isinstance(stmt, Assignment):
            continue
        call = stmt.value
        if not isinstance(call, FunctionCall):
            continue

        func_name = _resolve_func_name(call.func)
        if func_name not in ("input.int", "input.float", "input"):
            continue

        input_type = "int" if func_name == "input.int" else "float"

        # Extract kwargs
        defval = 0.0
        title = ""
        minval = None
        maxval = None
        step = None

        # defval can be positional arg[0] or kwarg
        if call.args:
            v = _eval_literal(call.args[0])
            if v is not None and not isinstance(v, str):
                defval = float(v)
        if "defval" in call.kwargs:
            v = _eval_literal(call.kwargs["defval"])
            if v is not None and not isinstance(v, str):
                defval = float(v)

        # title can be positional arg[1] (for input()) or kwarg
        if len(call.args) >= 2:
            v = _eval_literal(call.args[1])
            if isinstance(v, str):
                title = v
        if "title" in call.kwargs:
            v = _eval_literal(call.kwargs["title"])
            if isinstance(v, str):
                title = v

        if "minval" in call.kwargs:
            v = _eval_literal(call.kwargs["minval"])
            if v is not None and not isinstance(v, str):
                minval = float(v)

        if "maxval" in call.kwargs:
            v = _eval_literal(call.kwargs["maxval"])
            if v is not None and not isinstance(v, str):
                maxval = float(v)

        if "step" in call.kwargs:
            v = _eval_literal(call.kwargs["step"])
            if v is not None and not isinstance(v, str):
                step = float(v)

        # Build the key the same way input_fn.py does
        if not title:
            title = (
                f"input_{input_type}_{int(defval) if input_type == 'int' else defval}"
            )

        inputs.append(
            InputParam(
                var_name=stmt.target,
                title=title,
                input_type=input_type,
                defval=defval,
                minval=minval,
                maxval=maxval,
                step=step,
            )
        )

    return inputs


def generate_grid(
    inputs: list[InputParam],
    max_combinations: int = 50000,
) -> list[dict[str, float]]:
    """Generate a parameter grid from extracted input metadata.

    For inputs without ``minval``/``maxval``, auto-generates a range of
    ``defval ± 50%`` with a reasonable step size.

    Parameters
    ----------
    inputs : list[InputParam]
        Extracted input parameters.
    max_combinations : int
        Safety cap — if the grid exceeds this, step sizes are widened.

    Returns
    -------
    list[dict[str, float]]
        Each dict maps input title → value for one grid point.
    """
    if not inputs:
        return [{}]

    param_ranges: list[list[tuple[str, float]]] = []

    for inp in inputs:
        lo = inp.minval
        hi = inp.maxval
        st = inp.step

        # Auto-generate bounds if not specified
        if lo is None or hi is None:
            half = max(abs(inp.defval) * 0.5, 2.0)
            lo = lo if lo is not None else max(1, inp.defval - half)
            hi = hi if hi is not None else inp.defval + half

        if st is None:
            # Auto-step: aim for ~10 points
            raw = (hi - lo) / 10
            if inp.input_type == "int":
                st = max(1.0, round(raw))
            else:
                st = max(0.1, raw)

        # Ensure lo <= hi
        if lo > hi:
            lo, hi = hi, lo

        # Generate values
        vals: list[float] = []
        v = lo
        while v <= hi + st * 0.01:
            if inp.input_type == "int":
                vals.append(float(int(round(v))))
            else:
                vals.append(round(v, 6))
            v += st

        # Ensure defval is in the list
        if inp.input_type == "int":
            dv = float(int(inp.defval))
        else:
            dv = round(inp.defval, 6)
        if dv not in vals and lo <= dv <= hi:
            vals.append(dv)
            vals.sort()

        param_ranges.append([(inp.title, v) for v in vals])

    # Check total combinations
    total = 1
    for r in param_ranges:
        total *= len(r)

    if total > max_combinations:
        # Widen steps by reducing each range proportionally
        factor = (total / max_combinations) ** (1.0 / len(param_ranges))
        new_ranges: list[list[tuple[str, float]]] = []
        for r in param_ranges:
            keep = max(2, int(len(r) / factor))
            step = max(1, len(r) // keep)
            new_ranges.append(r[::step])
        param_ranges = new_ranges

    # Build grid via cartesian product
    grid: list[dict[str, float]] = []
    for combo in itertools.product(*param_ranges):
        grid.append({title: val for title, val in combo})

    return grid


def _calculate_sharpe(
    equity_curve: list[float], periods_per_year: float = 35040
) -> float:
    """Calculate annualised Sharpe ratio from an equity curve."""
    if len(equity_curve) < 2:
        return 0.0

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)

    if not returns:
        return 0.0

    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std_r = math.sqrt(var_r) if var_r > 0 else 0.0

    if std_r == 0:
        return 0.0

    return (mean_r / std_r) * math.sqrt(periods_per_year)


def _calculate_max_drawdown(equity_curve: list[float]) -> float:
    """Calculate maximum drawdown from peak."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def run_optimization(
    ast: Script,
    bars: list[BarData],
    grid: list[dict[str, float]],
    warmup_count: int = 0,
    metric: str = "sharpe",
) -> list[OptResult]:
    """Run grid search optimisation over a Pine script.

    Parameters
    ----------
    ast : Script
        Parsed Pine AST.
    bars : list[BarData]
        OHLCV bars (including warmup bars at the front).
    grid : list[dict[str, float]]
        Parameter grid — each dict maps input title → override value.
    warmup_count : int
        Number of leading bars that are warmup-only (not counted in metrics).
    metric : str
        Metric to sort by: ``"sharpe"`` (default), ``"return"``, ``"profit_factor"``.

    Returns
    -------
    list[OptResult]
        Results sorted by the chosen metric (best first).
    """
    results: list[OptResult] = []

    for i, params in enumerate(grid):
        ctx = ExecutionContext(bars=list(bars))
        ctx.inputs = dict(params)  # Override input values
        runtime = PineRuntime(ctx)
        result = runtime.run(ast)

        eq = result.equity_curve
        trades = result.trades
        initial = result.initial_capital

        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
        pf = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (999.99 if gross_profit > 0 else 0.0)
        )

        sharpe = _calculate_sharpe(eq)
        max_dd = _calculate_max_drawdown(eq)

        results.append(
            OptResult(
                params=params,
                net_profit=result.net_profit,
                return_pct=result.net_profit / initial if initial > 0 else 0.0,
                total_trades=result.total_trades,
                win_rate=result.win_rate,
                sharpe=sharpe,
                max_drawdown=max_dd,
                profit_factor=pf,
                equity_curve=eq,
            )
        )

    # Sort by chosen metric
    if metric == "return":
        results.sort(key=lambda r: r.return_pct, reverse=True)
    elif metric == "profit_factor":
        results.sort(key=lambda r: r.profit_factor, reverse=True)
    else:
        results.sort(key=lambda r: r.sharpe, reverse=True)

    return results
