"""Pine Script runtime — bar-by-bar execution engine.

Loads a parsed AST, iterates over OHLCV data, evaluates the script on each bar,
and collects strategy orders. Orders fill on the NEXT bar's open by default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from quantforge.pine.interpreter.builtins.strategy import StrategyEngine, Trade
from quantforge.pine.interpreter.context import ExecutionContext
from quantforge.pine.interpreter.series import PineSeries
from quantforge.pine.parser.ast_nodes import (
    Assignment,
    BinaryOp,
    BoolLiteral,
    BreakStmt,
    ColorLiteral,
    ContinueStmt,
    FloatLiteral,
    ForLoop,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfExpr,
    IntLiteral,
    MultiVarDecl,
    NaLiteral,
    NamespacedId,
    Script,
    SeriesIndex,
    StringLiteral,
    SwitchExpr,
    TernaryOp,
    UnaryOp,
    VarDecl,
    WhileLoop,
)


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


@dataclass
class BacktestResult:
    """Result of running a Pine Script backtest."""

    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    initial_capital: float = 10000.0
    final_equity: float = 10000.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    inputs: dict[str, object] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Initial Capital: {self.initial_capital:.2f}",
            f"Final Equity:    {self.final_equity:.2f}",
            f"Total PnL:       {self.total_pnl:.2f}",
            f"Total Trades:    {self.total_trades}",
            f"Win Rate:        {self.win_rate:.1%}",
            f"Max Drawdown:    {self.max_drawdown:.2%}",
            f"Sharpe Ratio:    {self.sharpe_ratio:.3f}",
        ]
        return "\n".join(lines)


class PineRuntime:
    """Bar-by-bar Pine Script execution engine.

    Usage:
        parser = PineParser()
        ast = parser.parse(pine_source)
        runtime = PineRuntime(ast)
        result = runtime.run(ohlcv_df)
    """

    def __init__(
        self,
        script: Script,
        initial_capital: float = 10000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        input_overrides: dict[str, object] | None = None,
    ):
        self.script = script
        self.strategy_engine = StrategyEngine(
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
        )
        self.ctx = ExecutionContext(
            strategy_engine=self.strategy_engine,
            input_overrides=input_overrides,
        )
        # Call-site counter for unique ta.* instance keys
        self._call_counter = 0

    def run(self, data: pd.DataFrame) -> BacktestResult:
        """Run the Pine script on OHLCV data.

        Args:
            data: DataFrame with columns: open, high, low, close, volume.
                  Optional: time (unix timestamp).

        Returns:
            BacktestResult with trades, equity curve, and metrics.
        """
        # Validate data
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        total_bars = len(data)
        opens = data["open"].values
        highs = data["high"].values
        lows = data["low"].values
        closes = data["close"].values
        volumes = data["volume"].values
        times = data["time"].values if "time" in data.columns else [0] * total_bars

        # First pass: scan for function definitions and strategy() declaration
        for node in self.script.body:
            if isinstance(node, FunctionDef):
                self.ctx._user_functions[node.name] = node
            elif isinstance(node, FunctionCall):
                name = self._get_call_name(node)
                if name == "strategy":
                    self._eval_node(node)

        # Bar-by-bar execution
        for i in range(total_bars):
            # Reset call counter for deterministic ta.* instance keys each bar
            self._call_counter = 0

            # Push OHLCV
            self.ctx.begin_bar(
                open_=float(opens[i]),
                high=float(highs[i]),
                low=float(lows[i]),
                close=float(closes[i]),
                volume=float(volumes[i]),
                time_val=int(times[i]),
                total_bars=total_bars,
            )

            # Process pending orders from PREVIOUS bar (fill on current bar's open)
            if i > 0 and not self.strategy_engine.process_orders_on_close:
                self.strategy_engine.process_orders(
                    float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
                )

            # Evaluate script body
            for node in self.script.body:
                if isinstance(node, FunctionDef):
                    continue  # Already registered
                self._eval_node(node)

            # If process_orders_on_close, fill at current bar close
            if self.strategy_engine.process_orders_on_close:
                self.strategy_engine.process_orders(
                    float(closes[i]), float(highs[i]), float(lows[i]), float(closes[i])
                )

            self.ctx.end_bar()

        # Process any remaining orders on last bar
        if total_bars > 0 and self.strategy_engine.pending_orders:
            self.strategy_engine.process_orders(
                float(closes[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
            )

        return self._build_result()

    # ------------------------------------------------------------------
    # AST evaluation
    # ------------------------------------------------------------------

    def _eval_node(self, node) -> Any:
        """Evaluate an AST node and return its value."""
        if node is None:
            return None

        if isinstance(node, IntLiteral):
            return node.value
        if isinstance(node, FloatLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, ColorLiteral):
            return node.value
        if isinstance(node, NaLiteral):
            return None

        if isinstance(node, Identifier):
            return self.ctx.get_var(node.name)

        if isinstance(node, NamespacedId):
            return self._eval_namespaced(node)

        if isinstance(node, SeriesIndex):
            return self._eval_series_index(node)

        if isinstance(node, BinaryOp):
            return self._eval_binary_op(node)

        if isinstance(node, UnaryOp):
            return self._eval_unary_op(node)

        if isinstance(node, TernaryOp):
            cond = self._eval_node(node.condition)
            if _is_truthy(cond):
                return self._eval_node(node.true_expr)
            return self._eval_node(node.false_expr)

        if isinstance(node, FunctionCall):
            return self._eval_function_call(node)

        if isinstance(node, VarDecl):
            return self._eval_var_decl(node)

        if isinstance(node, MultiVarDecl):
            return self._eval_multi_var_decl(node)

        if isinstance(node, Assignment):
            return self._eval_assignment(node)

        if isinstance(node, IfExpr):
            return self._eval_if(node)

        if isinstance(node, ForLoop):
            return self._eval_for(node)

        if isinstance(node, WhileLoop):
            return self._eval_while(node)

        if isinstance(node, SwitchExpr):
            return self._eval_switch(node)

        if isinstance(node, BreakStmt):
            raise _BreakSignal()

        if isinstance(node, ContinueStmt):
            raise _ContinueSignal()

        if isinstance(node, FunctionDef):
            self.ctx._user_functions[node.name] = node
            return None

        return None

    def _eval_namespaced(self, node: NamespacedId) -> Any:
        """Evaluate a namespaced identifier (ta.sma, strategy.long, etc.)."""
        ns = node.namespace
        member = node.member

        if ns == "strategy":
            return self.ctx.resolve_strategy_property(member)

        if ns == "barstate":
            return getattr(self.ctx.barstate, member, None)

        if ns == "color":
            return node.value if hasattr(node, "value") else member

        # Could be a variable with dotted access
        return None

    def _eval_series_index(self, node: SeriesIndex) -> Any:
        """Evaluate series[offset] access."""
        series = self._eval_node(node.series)
        offset = self._eval_node(node.offset)

        if isinstance(series, PineSeries) and offset is not None:
            return series[int(offset)]

        return None

    def _eval_binary_op(self, node: BinaryOp) -> Any:
        op = node.op
        left = self._eval_node(node.left)

        # Short-circuit for logical operators
        if op == "and":
            if not _is_truthy(left):
                return False
            right = self._eval_node(node.right)
            return _is_truthy(right)

        if op == "or":
            if _is_truthy(left):
                return True
            right = self._eval_node(node.right)
            return _is_truthy(right)

        right = self._eval_node(node.right)

        # na propagation for arithmetic
        if op in ("+", "-", "*", "/", "%"):
            if left is None or right is None:
                return None
            left = _to_number(left)
            right = _to_number(right)
            if left is None or right is None:
                return None
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                if right == 0:
                    return None
                return left / right
            if op == "%":
                if right == 0:
                    return None
                return left % right

        # Comparison operators
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op in ("<", ">", "<=", ">="):
            if left is None or right is None:
                return False
            left = _to_number(left)
            right = _to_number(right)
            if left is None or right is None:
                return False
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            if op == ">=":
                return left >= right

        return None

    def _eval_unary_op(self, node: UnaryOp) -> Any:
        val = self._eval_node(node.operand)
        if node.op == "-":
            if val is None:
                return None
            return -_to_number(val)
        if node.op == "not":
            return not _is_truthy(val)
        return val

    def _eval_function_call(self, node: FunctionCall) -> Any:
        """Evaluate a function call."""
        name_node = node.name

        # Evaluate arguments
        args = [self._eval_node(a) for a in node.args]
        kwargs = {k: self._eval_node(v) for k, v in node.kwargs.items()}

        # Namespaced calls
        if isinstance(name_node, NamespacedId):
            ns = name_node.namespace
            member = name_node.member

            if ns == "ta":
                self._call_counter += 1
                call_key = f"ta.{member}_{self._call_counter}_{node.line}"
                return self.ctx.resolve_ta_call(member, args, kwargs, call_key)

            if ns == "math":
                return self.ctx.resolve_math_call(member, args, kwargs)

            if ns == "strategy":
                return self.ctx.resolve_strategy_call(member, args, kwargs)

            if ns == "input":
                return self.ctx.resolve_input_call(member, args, kwargs)

            if ns == "str":
                return self.ctx.resolve_builtin_call(f"str.{member}", args, kwargs)

            return None

        # Top-level function calls
        if isinstance(name_node, Identifier):
            fname = name_node.name

            # Built-in functions
            if fname in ("na", "nz", "fixnan", "alert", "strategy", "input"):
                return self.ctx.resolve_builtin_call(fname, args, kwargs)

            # User-defined functions
            if fname in self.ctx._user_functions:
                return self._call_user_function(fname, args, kwargs)

            return None

        return None

    def _call_user_function(self, name: str, args: list, kwargs: dict) -> Any:
        """Call a user-defined Pine function."""
        func_def = self.ctx._user_functions[name]
        local_vars: dict[str, Any] = {}

        # Bind parameters
        for i, param in enumerate(func_def.params):
            if i < len(args):
                local_vars[param] = args[i]
            elif param in kwargs:
                local_vars[param] = kwargs[param]
            elif param in func_def.default_values:
                local_vars[param] = self._eval_node(func_def.default_values[param])
            else:
                local_vars[param] = None

        self.ctx.push_scope(local_vars)
        result = None
        for stmt in func_def.body:
            result = self._eval_node(stmt)
        self.ctx.pop_scope()
        return result

    def _eval_var_decl(self, node: VarDecl) -> Any:
        value = self._eval_node(node.value)
        self.ctx.set_var(node.name, value, qualifier=node.qualifier)
        return value

    def _eval_multi_var_decl(self, node: MultiVarDecl) -> Any:
        value = self._eval_node(node.value)
        if isinstance(value, tuple):
            for i, name in enumerate(node.names):
                self.ctx.set_var(name, value[i] if i < len(value) else None)
        return value

    def _eval_assignment(self, node: Assignment) -> Any:
        value = self._eval_node(node.value)
        if node.op == ":=":
            self.ctx.update_var(node.target, value)
        elif node.op == "+=":
            cur = self.ctx.get_var(node.target)
            self.ctx.update_var(
                node.target, (_to_number(cur) or 0) + (_to_number(value) or 0)
            )
        elif node.op == "-=":
            cur = self.ctx.get_var(node.target)
            self.ctx.update_var(
                node.target, (_to_number(cur) or 0) - (_to_number(value) or 0)
            )
        elif node.op == "*=":
            cur = self.ctx.get_var(node.target)
            self.ctx.update_var(
                node.target, (_to_number(cur) or 0) * (_to_number(value) or 0)
            )
        elif node.op == "/=":
            cur = self.ctx.get_var(node.target)
            d = _to_number(value) or 0
            self.ctx.update_var(
                node.target, (_to_number(cur) or 0) / d if d != 0 else None
            )
        else:
            self.ctx.set_var(node.target, value)
        return value

    def _eval_if(self, node: IfExpr) -> Any:
        cond = self._eval_node(node.condition)
        if _is_truthy(cond):
            return self._eval_block(node.then_body)

        for elif_cond, elif_body in node.elif_clauses:
            if _is_truthy(self._eval_node(elif_cond)):
                return self._eval_block(elif_body)

        if node.else_body:
            return self._eval_block(node.else_body)

        return None

    def _eval_for(self, node: ForLoop) -> Any:
        start = int(self._eval_node(node.start) or 0)
        stop = int(self._eval_node(node.stop) or 0)
        step = int(self._eval_node(node.step) or 1) if node.step else 1

        if step == 0:
            return None

        result = None
        i = start
        max_iterations = 100000  # safety limit
        count = 0

        while (step > 0 and i <= stop) or (step < 0 and i >= stop):
            if count >= max_iterations:
                break
            count += 1
            self.ctx.set_var(node.var, i)
            try:
                result = self._eval_block(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                pass
            i += step

        return result

    def _eval_while(self, node: WhileLoop) -> Any:
        result = None
        max_iterations = 100000
        count = 0

        while _is_truthy(self._eval_node(node.condition)):
            if count >= max_iterations:
                break
            count += 1
            try:
                result = self._eval_block(node.body)
            except _BreakSignal:
                break
            except _ContinueSignal:
                pass

        return result

    def _eval_switch(self, node: SwitchExpr) -> Any:
        expr_val = self._eval_node(node.expr) if node.expr else None

        for case_cond, case_body in node.cases:
            if case_cond is None:
                # Default case
                return self._eval_block(case_body)
            case_val = self._eval_node(case_cond)
            if node.expr is not None:
                if case_val == expr_val:
                    return self._eval_block(case_body)
            else:
                if _is_truthy(case_val):
                    return self._eval_block(case_body)

        return None

    def _eval_block(self, stmts: list) -> Any:
        result = None
        for stmt in stmts:
            result = self._eval_node(stmt)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_call_name(self, node: FunctionCall) -> str:
        if isinstance(node.name, Identifier):
            return node.name.name
        if isinstance(node.name, NamespacedId):
            return f"{node.name.namespace}.{node.name.member}"
        return ""

    def _build_result(self) -> BacktestResult:
        """Build the final BacktestResult from strategy state."""
        trades = self.strategy_engine.trades
        equity_curve = self.strategy_engine.equity_curve
        total = len(trades)
        winners = sum(1 for t in trades if t.pnl > 0)
        losers = sum(1 for t in trades if t.pnl < 0)
        total_pnl = sum(t.pnl - t.commission for t in trades)
        final_equity = self.strategy_engine.initial_capital + total_pnl

        # Max drawdown
        max_dd = 0.0
        if equity_curve:
            peak = equity_curve[0]
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

        # Sharpe ratio (simplified)
        sharpe = 0.0
        if len(equity_curve) > 1:
            returns = []
            for i in range(1, len(equity_curve)):
                if equity_curve[i - 1] > 0:
                    returns.append(equity_curve[i] / equity_curve[i - 1] - 1)
            if returns:
                avg_ret = sum(returns) / len(returns)
                std_ret = (
                    sum((r - avg_ret) ** 2 for r in returns) / len(returns)
                ) ** 0.5
                if std_ret > 0:
                    sharpe = avg_ret / std_ret * (252**0.5)

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.strategy_engine.initial_capital,
            final_equity=final_equity,
            total_trades=total,
            winning_trades=winners,
            losing_trades=losers,
            win_rate=winners / total if total > 0 else 0.0,
            total_pnl=total_pnl,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            inputs=self.ctx.input_manager.all_inputs,
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _is_truthy(v: Any) -> bool:
    """Pine Script truthiness: None/na/0/false are falsy."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return False
        return v != 0
    return bool(v)


def _to_number(v: Any) -> float | None:
    """Convert value to float, returning None for non-numeric."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, PineSeries):
        return _to_number(v.current)
    return None
