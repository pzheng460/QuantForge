"""Bar-by-bar execution engine for Pine Script AST.

Walks the AST on each bar, evaluating expressions, managing series,
and executing strategy orders with next-bar-open semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quantforge.pine.interpreter.builtins import ta, math_fn, input_fn
from quantforge.pine.interpreter.builtins.strategy import (
    Direction,
    StrategyContext,
)
from quantforge.pine.interpreter.context import ExecutionContext
from quantforge.pine.interpreter.series import PineSeries, is_na, nz
from quantforge.pine.parser.ast_nodes import (
    Assignment,
    ASTNode,
    BinOp,
    BoolLiteral,
    BreakStmt,
    ColorLiteral,
    ContinueStmt,
    ForLoop,
    ForInLoop,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfExpr,
    IndexAccess,
    IndicatorDecl,
    MemberAccess,
    NaLiteral,
    NumberLiteral,
    Script,
    StrategyDecl,
    StringLiteral,
    TernaryOp,
    TupleAssignment,
    UnaryOp,
    WhileLoop,
)


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


@dataclass
class BacktestResult:
    """Result of running a Pine Script strategy on historical data."""

    trades: list = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    net_profit: float = 0.0
    initial_capital: float = 100000.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0


class PineRuntime:
    """Bar-by-bar Pine Script execution engine."""

    def __init__(self, ctx: ExecutionContext) -> None:
        self.ctx = ctx
        self.strategy_ctx: StrategyContext | None = None
        self._series_assignments: dict[str, PineSeries] = {}

    def run(self, script: Script) -> BacktestResult:
        """Execute a parsed Pine Script on the data in the context."""
        ta.reset_calculators(id(self.ctx))

        # Process declarations
        for decl in script.declarations:
            self._eval_declaration(decl)

        # Bar-by-bar execution
        while self.ctx.advance_bar():
            # Execute pending orders at this bar's open
            if self.strategy_ctx and self.ctx.bar_index > 0:
                bar = self.ctx.current_bar
                self.strategy_ctx.execute_pending(bar.open, self.ctx.bar_index)

            # Execute script body
            for stmt in script.body:
                self._exec(stmt)

            # Update equity tracking
            if self.strategy_ctx:
                bar = self.ctx.current_bar
                self.strategy_ctx.update_equity(bar.close)

        # Close any remaining position at last bar close
        if self.strategy_ctx and not self.strategy_ctx.position.is_flat:
            last_bar = self.ctx.bars[-1] if self.ctx.bars else None
            if last_bar:
                self.strategy_ctx._close_position(
                    last_bar.close, self.ctx.bar_index, comment="end_of_data"
                )

        return self._build_result()

    def _build_result(self) -> BacktestResult:
        if not self.strategy_ctx:
            return BacktestResult()

        sc = self.strategy_ctx
        trades = sc.trades
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        total = len(trades)

        return BacktestResult(
            trades=trades,
            equity_curve=sc.equity_curve,
            net_profit=sc.net_profit,
            initial_capital=sc.initial_capital,
            total_trades=total,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=len(winning) / total if total > 0 else 0.0,
        )

    # --- Declaration handling ---

    def _eval_declaration(self, node: ASTNode) -> None:
        if isinstance(node, StrategyDecl):
            kwargs = {k: self._eval(v) for k, v in node.kwargs.items()}
            _title = kwargs.get("title", "Strategy")  # noqa: F841
            initial_capital = kwargs.get("initial_capital", 100000.0)
            default_qty = kwargs.get("default_qty_value", 1.0)
            commission = kwargs.get("commission_value", 0.0)
            pyramiding = kwargs.get("pyramiding", 1)
            # Commission type handling
            comm_type = kwargs.get("commission_type", None)
            if comm_type and "percent" in str(comm_type).lower():
                commission = commission / 100.0

            self.strategy_ctx = StrategyContext(
                initial_capital=float(initial_capital),
                default_qty=float(default_qty),
                commission=float(commission),
                pyramiding=int(pyramiding),
            )
            self.ctx.strategy_context = self.strategy_ctx

        elif isinstance(node, IndicatorDecl):
            pass  # Indicators don't need special setup

    # --- Statement execution ---

    def _exec(self, node: ASTNode) -> object:
        if isinstance(node, Assignment):
            return self._exec_assignment(node)
        elif isinstance(node, TupleAssignment):
            return self._exec_tuple_assignment(node)
        elif isinstance(node, IfExpr):
            return self._exec_if(node)
        elif isinstance(node, ForLoop):
            return self._exec_for(node)
        elif isinstance(node, ForInLoop):
            return self._exec_for_in(node)
        elif isinstance(node, WhileLoop):
            return self._exec_while(node)
        elif isinstance(node, FunctionDef):
            return self._exec_funcdef(node)
        elif isinstance(node, BreakStmt):
            raise _BreakSignal()
        elif isinstance(node, ContinueStmt):
            raise _ContinueSignal()
        else:
            return self._eval(node)

    def _exec_assignment(self, node: Assignment) -> None:
        value = self._eval(node.value)

        if node.op == ":=":
            self.ctx._variables[node.target] = value
        elif node.op == "+=":
            old = self.ctx.get_var(node.target) or 0
            self.ctx._variables[node.target] = old + value
        elif node.op == "-=":
            old = self.ctx.get_var(node.target) or 0
            self.ctx._variables[node.target] = old - value
        elif node.op == "*=":
            old = self.ctx.get_var(node.target) or 0
            self.ctx._variables[node.target] = old * value
        elif node.op == "/=":
            old = self.ctx.get_var(node.target) or 1
            self.ctx._variables[node.target] = old / value if value else old
        else:
            self.ctx.set_var(node.target, value, declaration=node.declaration)

        # Track as series for history reference
        if node.target not in self.ctx._series or node.target not in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "time",
            "bar_index",
        ):
            self.ctx.set_series_value(node.target, value)

    def _exec_tuple_assignment(self, node: TupleAssignment) -> None:
        value = self._eval(node.value)
        if isinstance(value, (list, tuple)):
            for i, target in enumerate(node.targets):
                v = value[i] if i < len(value) else None
                self.ctx.set_var(target, v)
                self.ctx.set_series_value(target, v)

    def _exec_if(self, node: IfExpr) -> object:
        cond = self._eval(node.condition)
        if cond:
            result = None
            for stmt in node.body:
                result = self._exec(stmt)
            return result

        for elseif_cond, elseif_body in node.elseif_clauses:
            if self._eval(elseif_cond):
                result = None
                for stmt in elseif_body:
                    result = self._exec(stmt)
                return result

        if node.else_body:
            result = None
            for stmt in node.else_body:
                result = self._exec(stmt)
            return result
        return None

    def _exec_for(self, node: ForLoop) -> object:
        start = int(self._eval(node.start))
        stop = int(self._eval(node.stop))
        step = int(self._eval(node.step)) if node.step else 1
        result = None

        for i in range(start, stop + 1, step):
            self.ctx._variables[node.var] = i
            try:
                for stmt in node.body:
                    result = self._exec(stmt)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue
        return result

    def _exec_for_in(self, node: ForInLoop) -> object:
        iterable = self._eval(node.iterable)
        result = None
        for item in iterable:
            self.ctx._variables[node.var] = item
            try:
                for stmt in node.body:
                    result = self._exec(stmt)
            except _BreakSignal:
                break
            except _ContinueSignal:
                continue
        return result

    def _exec_while(self, node: WhileLoop) -> object:
        result = None
        max_iter = 100000
        i = 0
        while self._eval(node.condition) and i < max_iter:
            try:
                for stmt in node.body:
                    result = self._exec(stmt)
            except _BreakSignal:
                break
            except _ContinueSignal:
                pass
            i += 1
        return result

    def _exec_funcdef(self, node: FunctionDef) -> None:
        self.ctx.functions[node.name] = node

    # --- Expression evaluation ---

    def _eval(self, node: ASTNode) -> object:
        if isinstance(node, NumberLiteral):
            return node.value
        elif isinstance(node, StringLiteral):
            return node.value
        elif isinstance(node, BoolLiteral):
            return node.value
        elif isinstance(node, NaLiteral):
            return None
        elif isinstance(node, ColorLiteral):
            return node.value
        elif isinstance(node, Identifier):
            return self._eval_identifier(node)
        elif isinstance(node, MemberAccess):
            return self._eval_member_access(node)
        elif isinstance(node, IndexAccess):
            return self._eval_index_access(node)
        elif isinstance(node, BinOp):
            return self._eval_binop(node)
        elif isinstance(node, UnaryOp):
            return self._eval_unaryop(node)
        elif isinstance(node, TernaryOp):
            cond = self._eval(node.condition)
            return self._eval(node.true_expr) if cond else self._eval(node.false_expr)
        elif isinstance(node, FunctionCall):
            return self._eval_call(node)
        elif isinstance(node, IfExpr):
            return self._exec_if(node)
        elif isinstance(node, Assignment):
            self._exec_assignment(node)
            return self.ctx.get_var(node.target)
        else:
            return None

    def _eval_identifier(self, node: Identifier) -> object:
        name = node.name
        # Check variables first
        if name in self.ctx._variables:
            return self.ctx._variables[name]
        # Check series
        if name in self.ctx._series:
            return self.ctx._series[name].current
        # Built-in constants
        builtins_map = {
            "na": None,
            "true": True,
            "false": False,
        }
        if name in builtins_map:
            return builtins_map[name]
        return None

    def _eval_member_access(self, node: MemberAccess) -> object:
        # Handle strategy.long, strategy.short constants
        if isinstance(node.obj, Identifier):
            ns = node.obj.name
            member = node.member

            if ns == "strategy":
                if member == "long":
                    return "long"
                elif member == "short":
                    return "short"
                elif member == "position_size":
                    return self.strategy_ctx.position_size if self.strategy_ctx else 0
                elif member == "position_avg_price":
                    return (
                        self.strategy_ctx.position.entry_price
                        if self.strategy_ctx
                        else 0
                    )
                elif member == "equity":
                    return self.strategy_ctx.equity if self.strategy_ctx else 0
            elif ns == "color":
                return f"color.{member}"
            elif ns == "input":
                # Return a callable namespace marker
                return ("input", member)

        # General member access on evaluated object
        obj = self._eval(node.obj)
        if isinstance(obj, PineSeries):
            return obj  # The member was likely a namespace like ta.sma
        return obj

    def _eval_index_access(self, node: IndexAccess) -> object:
        offset = int(self._eval(node.index))
        # The object might be an identifier referring to a series
        if isinstance(node.obj, Identifier):
            name = node.obj.name
            if name in self.ctx._series:
                return self.ctx._series[name][offset]
            # Variable that is tracked as series
            series = self.ctx.get_series(name)
            return series[offset]
        # Could be a general expression
        obj = self._eval(node.obj)
        if isinstance(obj, PineSeries):
            return obj[offset]
        return None

    def _eval_binop(self, node: BinOp) -> object:
        left = self._eval(node.left)
        right = self._eval(node.right)

        if is_na(left) or is_na(right):
            if node.op in ("==", "!="):
                if node.op == "==":
                    return is_na(left) and is_na(right)
                return not (is_na(left) and is_na(right))
            return None

        op = node.op
        if op == "+":
            return left + right
        elif op == "-":
            return left - right
        elif op == "*":
            return left * right
        elif op == "/":
            return left / right if right != 0 else None
        elif op == "%":
            return left % right if right != 0 else None
        elif op == ">":
            return left > right
        elif op == "<":
            return left < right
        elif op == ">=":
            return left >= right
        elif op == "<=":
            return left <= right
        elif op == "==":
            return left == right
        elif op == "!=":
            return left != right
        elif op == "and":
            return bool(left) and bool(right)
        elif op == "or":
            return bool(left) or bool(right)
        return None

    def _eval_unaryop(self, node: UnaryOp) -> object:
        val = self._eval(node.operand)
        if node.op == "-":
            return -val if val is not None else None
        elif node.op == "not":
            return not val if val is not None else None
        return val

    def _eval_call(self, node: FunctionCall) -> object:
        args = [self._eval(a) for a in node.args]
        kwargs = {k: self._eval(v) for k, v in node.kwargs.items()}

        # Resolve function name
        func_name = self._resolve_func_name(node.func)

        # --- Built-in ta.* functions ---
        if func_name == "ta.sma":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_sma(self.ctx, source, length)

        elif func_name == "ta.ema":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_ema(self.ctx, source, length)

        elif func_name == "ta.rma":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_rma(self.ctx, source, length)

        elif func_name == "ta.rsi":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_rsi(self.ctx, source, length)

        elif func_name == "ta.atr":
            length = int(args[0]) if args else int(kwargs.get("length", 14))
            return ta.ta_atr(self.ctx, length)

        elif func_name == "ta.adx":
            length = int(args[0]) if args else int(kwargs.get("length", 14))
            return ta.ta_adx(self.ctx, length)

        elif func_name == "ta.macd":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            fast = int(args[1]) if len(args) > 1 else int(kwargs.get("fastlen", 12))
            slow = int(args[2]) if len(args) > 2 else int(kwargs.get("slowlen", 26))
            signal = int(args[3]) if len(args) > 3 else int(kwargs.get("signallen", 9))
            return ta.ta_macd(self.ctx, source, fast, slow, signal)

        elif func_name == "ta.bb":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 20))
            mult = float(args[2]) if len(args) > 2 else float(kwargs.get("mult", 2.0))
            return ta.ta_bbands(self.ctx, source, length, mult)

        elif func_name == "ta.stoch":
            k_len = int(args[0]) if args else int(kwargs.get("k", 14))
            k_smooth = int(args[1]) if len(args) > 1 else int(kwargs.get("ksma", 1))
            d_smooth = int(args[2]) if len(args) > 2 else int(kwargs.get("dsma", 3))
            return ta.ta_stoch(self.ctx, k_len, k_smooth, d_smooth)

        elif func_name == "ta.crossover":
            s_a = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            s_b = self._resolve_series_arg(
                node.args[1] if len(node.args) > 1 else None,
                args[1] if len(args) > 1 else None,
            )
            return ta.ta_crossover(s_a, s_b)

        elif func_name == "ta.crossunder":
            s_a = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            s_b = self._resolve_series_arg(
                node.args[1] if len(node.args) > 1 else None,
                args[1] if len(args) > 1 else None,
            )
            return ta.ta_crossunder(s_a, s_b)

        elif func_name == "ta.highest":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_highest(source, length)

        elif func_name == "ta.lowest":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 14))
            return ta.ta_lowest(source, length)

        elif func_name == "ta.change":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 1))
            return ta.ta_change(source, length)

        elif func_name == "ta.stdev":
            source = self._resolve_series_arg(
                node.args[0] if node.args else None, args[0] if args else None
            )
            length = int(args[1]) if len(args) > 1 else int(kwargs.get("length", 20))
            return ta.ta_stdev(self.ctx, source, length)

        elif func_name == "ta.tr":
            return ta.ta_tr(self.ctx)

        # --- math.* ---
        elif func_name == "math.abs":
            return math_fn.math_abs(args[0] if args else None)
        elif func_name == "math.max":
            return math_fn.math_max(*args)
        elif func_name == "math.min":
            return math_fn.math_min(*args)
        elif func_name == "math.round":
            return math_fn.math_round(
                args[0] if args else None, args[1] if len(args) > 1 else 0
            )
        elif func_name == "math.log":
            return math_fn.math_log(args[0] if args else None)
        elif func_name == "math.sqrt":
            return math_fn.math_sqrt(args[0] if args else None)
        elif func_name == "math.pow":
            return math_fn.math_pow(
                args[0] if args else None, args[1] if len(args) > 1 else None
            )
        elif func_name == "math.ceil":
            return math_fn.math_ceil(args[0] if args else None)
        elif func_name == "math.floor":
            return math_fn.math_floor(args[0] if args else None)
        elif func_name == "math.sign":
            return math_fn.math_sign(args[0] if args else None)

        # --- strategy.* ---
        elif func_name == "strategy.entry":
            if not self.strategy_ctx:
                return None
            entry_id = args[0] if args else kwargs.get("id", "entry")
            direction = args[1] if len(args) > 1 else kwargs.get("direction", "long")
            qty = args[2] if len(args) > 2 else kwargs.get("qty", None)
            limit = kwargs.get("limit", None)
            stop = kwargs.get("stop", None)
            comment = kwargs.get("comment", "")

            dir_enum = Direction.LONG if direction == "long" else Direction.SHORT
            self.strategy_ctx.place_entry(
                id=str(entry_id),
                direction=dir_enum,
                qty=float(qty) if qty else None,
                limit=float(limit) if limit else None,
                stop=float(stop) if stop else None,
                comment=str(comment),
                bar_index=self.ctx.bar_index,
            )
            return None

        elif func_name == "strategy.close":
            if not self.strategy_ctx:
                return None
            entry_id = args[0] if args else kwargs.get("id", "")
            comment = kwargs.get("comment", "")
            self.strategy_ctx.place_close(
                id=str(entry_id),
                comment=str(comment),
                bar_index=self.ctx.bar_index,
            )
            return None

        elif func_name == "strategy.exit":
            if not self.strategy_ctx:
                return None
            exit_id = args[0] if args else kwargs.get("id", "exit")
            from_entry = args[1] if len(args) > 1 else kwargs.get("from_entry", "")
            limit = kwargs.get("limit", None)
            stop = kwargs.get("stop", None)
            comment = kwargs.get("comment", "")
            self.strategy_ctx.place_exit(
                id=str(exit_id),
                from_entry=str(from_entry),
                limit=float(limit) if limit else None,
                stop=float(stop) if stop else None,
                comment=str(comment),
                bar_index=self.ctx.bar_index,
            )
            return None

        elif func_name == "strategy.close_all":
            if not self.strategy_ctx:
                return None
            comment = kwargs.get("comment", "")
            self.strategy_ctx.place_close_all(
                comment=str(comment),
                bar_index=self.ctx.bar_index,
            )
            return None

        # --- input.* ---
        elif func_name == "input.int":
            return input_fn.input_int(self.ctx, *args, **kwargs)
        elif func_name == "input.float":
            return input_fn.input_float(self.ctx, *args, **kwargs)
        elif func_name == "input.bool":
            return input_fn.input_bool(self.ctx, *args, **kwargs)
        elif func_name == "input.string":
            return input_fn.input_string(self.ctx, *args, **kwargs)
        elif func_name == "input.source" or func_name == "input":
            return input_fn.input_source(self.ctx, *args, **kwargs)

        # --- Built-in functions ---
        elif func_name == "nz":
            val = args[0] if args else None
            repl = args[1] if len(args) > 1 else 0
            return nz(val, repl)
        elif func_name == "na":
            if args:
                return is_na(args[0])
            return None
        elif func_name == "fixnan":
            # Return previous non-na value (simplified)
            return args[0] if args and not is_na(args[0]) else None
        elif func_name == "plot" or func_name == "plotshape" or func_name == "plotchar":
            return None  # Plotting is a no-op in backtest
        elif func_name == "alert" or func_name == "alertcondition":
            return None
        elif func_name == "bgcolor" or func_name == "barcolor":
            return None
        elif func_name == "str.tostring":
            return str(args[0]) if args else ""
        elif func_name == "array.new_float":
            size = int(args[0]) if args else 0
            val = args[1] if len(args) > 1 else 0.0
            return [val] * size

        # --- User-defined functions ---
        elif func_name in self.ctx.functions:
            return self._call_user_func(func_name, args, kwargs)

        return None

    def _call_user_func(self, name: str, args: list, kwargs: dict) -> object:
        func_def = self.ctx.functions[name]
        if not isinstance(func_def, FunctionDef):
            return None

        # Save current variables
        saved = dict(self.ctx._variables)

        # Bind parameters
        for i, param in enumerate(func_def.params):
            if i < len(args):
                self.ctx._variables[param.name] = args[i]
            elif param.name in kwargs:
                self.ctx._variables[param.name] = kwargs[param.name]
            elif param.default is not None:
                self.ctx._variables[param.name] = self._eval(param.default)

        # Execute body
        result = None
        for stmt in func_def.body:
            result = self._exec(stmt)

        # Restore variables (except function-local ones)
        local_names = {p.name for p in func_def.params}
        for name_var in local_names:
            if name_var in self.ctx._variables:
                del self.ctx._variables[name_var]
        for k, v in saved.items():
            if k not in self.ctx._variables:
                self.ctx._variables[k] = v

        return result

    # --- Helpers ---

    def _resolve_func_name(self, node: ASTNode) -> str:
        """Resolve a function call target to a dotted name string."""
        if isinstance(node, Identifier):
            return node.name
        elif isinstance(node, MemberAccess):
            obj_name = self._resolve_func_name(node.obj)
            return f"{obj_name}.{node.member}"
        return ""

    def _resolve_series_arg(
        self, ast_node: ASTNode | None, eval_value: object
    ) -> PineSeries:
        """Resolve a function argument to a PineSeries.

        If the argument is an identifier referring to a series, return that series.
        If it's a numeric value, return the close series (or wrap it).
        """
        if isinstance(ast_node, Identifier):
            name = ast_node.name
            if name in self.ctx._series:
                return self.ctx._series[name]
        if isinstance(eval_value, PineSeries):
            return eval_value
        # If it's a variable name that maps to a series
        if isinstance(ast_node, Identifier):
            return self.ctx.get_series(ast_node.name)
        # Fallback: return close series
        return self.ctx.get_series("close")
