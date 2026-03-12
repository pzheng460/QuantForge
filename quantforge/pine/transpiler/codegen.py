"""AST → QuantForge Python strategy code transpiler.

Converts a parsed Pine Script AST into Python code that uses the
QuantForge strategy framework (signal core pattern).
"""

from __future__ import annotations

from quantforge.pine.parser.ast_nodes import (
    Assignment,
    ASTNode,
    BinOp,
    BoolLiteral,
    BreakStmt,
    ColorLiteral,
    ContinueStmt,
    ForLoop,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfExpr,
    IndexAccess,
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


# Pine ta.* → QuantForge interpreter builtin function mappings
_TA_FUNC_MAP = {
    "ta.sma": "ta_sma",
    "ta.ema": "ta_ema",
    "ta.rma": "ta_rma",
    "ta.rsi": "ta_rsi",
    "ta.atr": "ta_atr",
    "ta.adx": "ta_adx",
    "ta.stdev": "ta_stdev",
    "ta.macd": "ta_macd",
    "ta.bb": "ta_bbands",
    "ta.stoch": "ta_stoch",
    "ta.crossover": "ta_crossover",
    "ta.crossunder": "ta_crossunder",
    "ta.highest": "ta_highest",
    "ta.lowest": "ta_lowest",
    "ta.change": "ta_change",
    "ta.tr": "ta_tr",
}

# Pine strategy.* → QuantForge order API mappings
_STRATEGY_MAP = {
    "strategy.entry": "self._submit_entry",
    "strategy.close": "self._submit_close",
    "strategy.exit": "self._submit_exit",
    "strategy.close_all": "self._submit_close_all",
}

# Pine strategy constants
_STRATEGY_CONST_MAP = {
    "strategy.long": '"long"',
    "strategy.short": '"short"',
}

# No-op functions (plotting, alerts, etc.)
_NOOP_FUNCS = {
    "plot",
    "plotshape",
    "plotchar",
    "plotcandle",
    "plotbar",
    "alert",
    "alertcondition",
    "bgcolor",
    "barcolor",
    "fill",
    "hline",
}

# Pine math.* → Python math mappings
_MATH_MAP = {
    "math.abs": "abs",
    "math.max": "max",
    "math.min": "min",
    "math.round": "round",
    "math.log": "math.log",
    "math.sqrt": "math.sqrt",
    "math.pow": "math.pow",
    "math.ceil": "math.ceil",
    "math.floor": "math.floor",
}


class PineCodeGen:
    """Transpile Pine AST to a runnable QuantForge Python strategy."""

    def __init__(self) -> None:
        self._indent_level = 0
        self._lines: list[str] = []
        self._ta_funcs_used: set[str] = set()
        self._uses_math = False
        self._inputs: list[dict] = []
        self._strategy_name = "PineStrategy"
        self._initial_capital = 100000.0

    def generate(self, script: Script) -> str:
        """Generate Python code from a Pine Script AST."""
        self._lines = []
        self._ta_funcs_used = set()
        self._uses_math = False
        self._inputs = []

        # Extract strategy metadata
        for decl in script.declarations:
            if isinstance(decl, StrategyDecl):
                title = decl.kwargs.get("title")
                if title and isinstance(title, StringLiteral):
                    raw = title.value.replace(" ", "").replace("-", "_")
                    self._strategy_name = "".join(
                        c for c in raw if c.isalnum() or c == "_"
                    )
                cap = decl.kwargs.get("initial_capital")
                if cap and isinstance(cap, NumberLiteral):
                    self._initial_capital = cap.value

        # First pass: collect ta functions and inputs
        for stmt in script.body:
            self._scan_deps(stmt)

        # Generate header
        self._emit("# Auto-generated from Pine Script by QuantForge transpiler")
        self._emit("# Modify as needed to integrate with the QuantForge engine.")
        self._emit("")
        self._emit("from __future__ import annotations")
        self._emit("")

        if self._uses_math:
            self._emit("import math")
            self._emit("")

        # Import ta builtins used
        if self._ta_funcs_used:
            self._emit("from quantforge.pine.interpreter.builtins.ta import (")
            self._indent_level += 1
            for func in sorted(self._ta_funcs_used):
                self._emit(f"{func},")
            self._indent_level -= 1
            self._emit(")")

        self._emit(
            "from quantforge.pine.interpreter.context import BarData, ExecutionContext"
        )
        self._emit(
            "from quantforge.pine.interpreter.runtime import PineRuntime, BacktestResult"
        )
        self._emit("from quantforge.pine.parser.parser import parse as pine_parse")
        self._emit("")

        # Embed the Pine source as a constant for the runner
        self._emit(f"STRATEGY_NAME = {self._strategy_name!r}")
        self._emit(f"INITIAL_CAPITAL = {self._initial_capital}")
        self._emit("")

        # Generate the strategy class
        self._emit(f"class {self._strategy_name}:")
        self._indent_level += 1
        self._emit(f'"""Transpiled Pine Script strategy: {self._strategy_name}.')
        self._emit("")
        self._emit("This class wraps the Pine interpreter to execute the strategy.")
        self._emit("Use the `run()` class method for backtesting with OHLCV data.")
        self._emit('"""')
        self._emit("")

        # Constructor with inputs as parameters
        params = ["self"]
        for inp in self._inputs:
            name = inp["name"]
            default = inp["default"]
            params.append(f"{name}={default!r}")

        self._emit(f"def __init__({', '.join(params)}):")
        self._indent_level += 1
        if self._inputs:
            for inp in self._inputs:
                # Pine line: {inp['pine_line']}
                self._emit(f"self.{inp['name']} = {inp['name']}")
        else:
            self._emit("pass")
        self._indent_level -= 1
        self._emit("")

        # Generate update method (signal logic as Python)
        self._emit(
            "def update(self, bar_index: int, open: float, high: float, low: float, close: float, volume: float) -> None:"
        )
        self._indent_level += 1
        self._emit('"""Per-bar signal logic transpiled from Pine Script."""')
        if not script.body:
            self._emit("pass")
        else:
            for stmt in script.body:
                self._gen_stmt(stmt)
        self._indent_level -= 1
        self._emit("")

        # Generate run class method
        self._emit("@classmethod")
        self._emit("def run(cls, bars: list[BarData], **kwargs) -> BacktestResult:")
        self._indent_level += 1
        self._emit('"""Run backtest using Pine interpreter on BarData list."""')
        self._emit("pine_source = cls._pine_source()")
        self._emit("ast = pine_parse(pine_source)")
        self._emit("ctx = ExecutionContext(bars=bars)")
        self._emit("runtime = PineRuntime(ctx)")
        self._emit("return runtime.run(ast)")
        self._indent_level -= 1
        self._emit("")

        # Embed Pine source
        self._emit("@staticmethod")
        self._emit("def _pine_source() -> str:")
        self._indent_level += 1
        self._emit('"""Original Pine Script source."""')
        self._emit("return '''")
        # We'll add a placeholder - in practice the transpiler user provides this
        self._emit("# Pine source not embedded - use the .pine file directly")
        self._emit("'''")
        self._indent_level -= 1

        self._indent_level -= 1
        self._emit("")

        # Convenience runner
        self._emit("")
        self._emit('if __name__ == "__main__":')
        self._indent_level += 1
        self._emit("import sys")
        self._emit(f"print(f'Strategy: {self._strategy_name}')")
        self._emit("print('Use quantforge.pine.cli to run backtests:')")
        self._emit("print('  python -m quantforge.pine.cli backtest <file.pine>')")
        self._indent_level -= 1
        self._emit("")

        return "\n".join(self._lines)

    def _emit(self, line: str) -> None:
        prefix = "    " * self._indent_level
        self._lines.append(f"{prefix}{line}")

    # --- Dependency scanning ---

    def _scan_deps(self, node: ASTNode) -> None:
        """Collect ta functions and input declarations from AST."""
        if isinstance(node, FunctionCall):
            name = self._resolve_name(node.func)
            if name in _TA_FUNC_MAP:
                self._ta_funcs_used.add(_TA_FUNC_MAP[name])
            if name.startswith("math."):
                self._uses_math = True
            if name.startswith("input."):
                self._collect_input(name, node)
            for arg in node.args:
                self._scan_deps(arg)
            for v in node.kwargs.values():
                self._scan_deps(v)
        elif isinstance(node, Assignment):
            # Track input assignments
            if isinstance(node.value, FunctionCall):
                fname = self._resolve_name(node.value.func)
                if fname.startswith("input."):
                    inp = self._make_input(fname, node.value)
                    if inp:
                        inp["name"] = node.target
                        # Avoid duplicates
                        if not any(i["name"] == node.target for i in self._inputs):
                            self._inputs.append(inp)
            self._scan_deps(node.value)
        elif isinstance(node, IfExpr):
            self._scan_deps(node.condition)
            for stmt in node.body:
                self._scan_deps(stmt)
            for cond, body in node.elseif_clauses:
                self._scan_deps(cond)
                for stmt in body:
                    self._scan_deps(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan_deps(stmt)
        elif isinstance(node, BinOp):
            self._scan_deps(node.left)
            self._scan_deps(node.right)
        elif isinstance(node, UnaryOp):
            self._scan_deps(node.operand)
        elif isinstance(node, TernaryOp):
            self._scan_deps(node.condition)
            self._scan_deps(node.true_expr)
            self._scan_deps(node.false_expr)
        elif isinstance(node, TupleAssignment):
            self._scan_deps(node.value)
        elif isinstance(node, ForLoop):
            for stmt in node.body:
                self._scan_deps(stmt)
        elif isinstance(node, WhileLoop):
            self._scan_deps(node.condition)
            for stmt in node.body:
                self._scan_deps(stmt)

    def _collect_input(self, func_name: str, node: FunctionCall) -> None:
        """Collect input declarations for constructor parameters."""
        pass  # Handled in _scan_deps via Assignment check

    def _make_input(self, func_name: str, node: FunctionCall) -> dict | None:
        """Extract input metadata from an input.* call."""
        defval = None
        title = ""
        if node.args:
            defval = self._eval_literal(node.args[0])
        if "defval" in node.kwargs:
            defval = self._eval_literal(node.kwargs["defval"])
        if "title" in node.kwargs:
            t = node.kwargs["title"]
            if isinstance(t, StringLiteral):
                title = t.value
        return {"name": "", "default": defval, "title": title, "pine_line": func_name}

    @staticmethod
    def _eval_literal(node: ASTNode) -> object:
        if isinstance(node, NumberLiteral):
            return node.value
        elif isinstance(node, StringLiteral):
            return node.value
        elif isinstance(node, BoolLiteral):
            return node.value
        elif isinstance(node, NaLiteral):
            return None
        return None

    # --- Statement generation ---

    def _gen_stmt(self, node: ASTNode) -> None:
        if isinstance(node, Assignment):
            expr_str = self._gen_expr(node.value)
            # Add comment for input assignments
            comment = ""
            if isinstance(node.value, FunctionCall):
                fname = self._resolve_name(node.value.func)
                if fname.startswith("input."):
                    comment = f"  # Pine: {node.target} = {fname}(...)"
            if node.op == ":=":
                self._emit(f"{node.target} = {expr_str}{comment}")
            elif node.op in ("+=", "-=", "*=", "/="):
                self._emit(f"{node.target} {node.op} {expr_str}{comment}")
            else:
                self._emit(f"{node.target} = {expr_str}{comment}")

        elif isinstance(node, TupleAssignment):
            targets = ", ".join(node.targets)
            expr_str = self._gen_expr(node.value)
            self._emit(f"{targets} = {expr_str}")

        elif isinstance(node, IfExpr):
            cond_str = self._gen_expr(node.condition)
            self._emit(f"if {cond_str}:")
            self._indent_level += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent_level -= 1
            for elseif_cond, elseif_body in node.elseif_clauses:
                self._emit(f"elif {self._gen_expr(elseif_cond)}:")
                self._indent_level += 1
                for stmt in elseif_body:
                    self._gen_stmt(stmt)
                self._indent_level -= 1
            if node.else_body:
                self._emit("else:")
                self._indent_level += 1
                for stmt in node.else_body:
                    self._gen_stmt(stmt)
                self._indent_level -= 1

        elif isinstance(node, ForLoop):
            start = self._gen_expr(node.start)
            stop = self._gen_expr(node.stop)
            step = self._gen_expr(node.step) if node.step else "1"
            self._emit(f"for {node.var} in range({start}, {stop} + 1, {step}):")
            self._indent_level += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent_level -= 1

        elif isinstance(node, WhileLoop):
            self._emit(f"while {self._gen_expr(node.condition)}:")
            self._indent_level += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent_level -= 1

        elif isinstance(node, BreakStmt):
            self._emit("break")

        elif isinstance(node, ContinueStmt):
            self._emit("continue")

        elif isinstance(node, FunctionDef):
            params = ", ".join(
                f"{p.name}={self._gen_expr(p.default)}" if p.default else p.name
                for p in node.params
            )
            self._emit(f"def {node.name}(self, {params}):")
            self._indent_level += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent_level -= 1

        elif isinstance(node, FunctionCall):
            code = self._gen_expr(node)
            if code and code != "None":
                self._emit(code)

        else:
            expr = self._gen_expr(node)
            if expr and expr != "None":
                self._emit(expr)

    # --- Expression generation ---

    def _gen_expr(self, node: ASTNode | None) -> str:
        if node is None:
            return "None"
        if isinstance(node, NumberLiteral):
            v = node.value
            return repr(int(v)) if v == int(v) else repr(v)
        elif isinstance(node, StringLiteral):
            return repr(node.value)
        elif isinstance(node, BoolLiteral):
            return "True" if node.value else "False"
        elif isinstance(node, NaLiteral):
            return "None"
        elif isinstance(node, ColorLiteral):
            return repr(node.value)
        elif isinstance(node, Identifier):
            return self._map_identifier(node.name)
        elif isinstance(node, MemberAccess):
            full_name = self._resolve_name(node)
            # Map strategy constants
            if full_name in _STRATEGY_CONST_MAP:
                return _STRATEGY_CONST_MAP[full_name]
            obj_str = self._gen_expr(node.obj)
            return f"{obj_str}.{node.member}"
        elif isinstance(node, IndexAccess):
            obj_str = self._gen_expr(node.obj)
            idx_str = self._gen_expr(node.index)
            return f"{obj_str}[{idx_str}]"
        elif isinstance(node, BinOp):
            left = self._gen_expr(node.left)
            right = self._gen_expr(node.right)
            return f"({left} {node.op} {right})"
        elif isinstance(node, UnaryOp):
            operand = self._gen_expr(node.operand)
            if node.op == "not":
                return f"(not {operand})"
            return f"({node.op}{operand})"
        elif isinstance(node, TernaryOp):
            cond = self._gen_expr(node.condition)
            true_e = self._gen_expr(node.true_expr)
            false_e = self._gen_expr(node.false_expr)
            return f"({true_e} if {cond} else {false_e})"
        elif isinstance(node, FunctionCall):
            return self._gen_func_call(node)
        return "None"

    def _gen_func_call(self, node: FunctionCall) -> str:
        func_name = self._resolve_name(node.func)

        # No-op functions (plotting, alerts)
        if func_name in _NOOP_FUNCS:
            return f"pass  # Pine: {func_name}(...)"

        # Map strategy calls
        if func_name in _STRATEGY_MAP:
            py_func = _STRATEGY_MAP[func_name]
            args_str = self._gen_call_args(node)
            return f"{py_func}({args_str})  # Pine: {func_name}"

        # Map ta.* calls
        if func_name in _TA_FUNC_MAP:
            py_func = _TA_FUNC_MAP[func_name]
            args_str = self._gen_call_args(node)
            return f"{py_func}(ctx, {args_str})"

        # Map math.* calls
        if func_name in _MATH_MAP:
            py_func = _MATH_MAP[func_name]
            args_str = self._gen_call_args(node)
            return f"{py_func}({args_str})"

        # input.* calls → use self parameter
        if func_name.startswith("input."):
            # In transpiled code, inputs come from constructor
            args_str = self._gen_call_args(node)
            return f"self._input({args_str})  # Pine: {func_name}"

        # Built-in helpers
        if func_name == "nz":
            args_str = self._gen_call_args(node)
            return f"(({self._gen_expr(node.args[0])}) if ({self._gen_expr(node.args[0])}) is not None else {self._gen_expr(node.args[1]) if len(node.args) > 1 else '0'})"
        if func_name == "na":
            if node.args:
                return f"({self._gen_expr(node.args[0])} is None)"
            return "None"

        # General function call
        args_str = self._gen_call_args(node)
        return f"{func_name}({args_str})"

    def _gen_call_args(self, node: FunctionCall) -> str:
        parts = []
        for arg in node.args:
            parts.append(self._gen_expr(arg))
        for k, v in node.kwargs.items():
            parts.append(f"{k}={self._gen_expr(v)}")
        return ", ".join(parts)

    def _resolve_name(self, node: ASTNode) -> str:
        if isinstance(node, Identifier):
            return node.name
        elif isinstance(node, MemberAccess):
            obj = self._resolve_name(node.obj)
            return f"{obj}.{node.member}"
        return ""

    def _map_identifier(self, name: str) -> str:
        mapping = {
            "close": "close",
            "open": "open",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "bar_index": "bar_index",
            "na": "None",
        }
        return mapping.get(name, name)

    # --- Indicator scanning (legacy, now in _scan_deps) ---

    def _scan_indicators(self, node: ASTNode) -> None:
        self._scan_deps(node)


def transpile(script: Script) -> str:
    """Transpile a Pine Script AST to Python strategy code."""
    return PineCodeGen().generate(script)
