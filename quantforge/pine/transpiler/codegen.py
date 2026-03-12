"""AST → QuantForge Python strategy code transpiler.

Converts a parsed Pine Script AST into Python code that uses the
QuantForge strategy framework.
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


# Pine → Python name mappings
_TA_MAP = {
    "ta.sma": "StreamingSMA",
    "ta.ema": "StreamingEMA",
    "ta.rsi": "StreamingRSI",
    "ta.atr": "StreamingATR",
    "ta.adx": "StreamingADX",
    "ta.bb": "StreamingBB",
}

_STRATEGY_MAP = {
    "strategy.entry": "self._open_position",
    "strategy.close": "self._close_position",
    "strategy.exit": "self._close_position",
    "strategy.close_all": "self._close_all_positions",
}


class PineCodeGen:
    """Transpile Pine AST to Python strategy code."""

    def __init__(self) -> None:
        self._indent_level = 0
        self._lines: list[str] = []
        self._indicators: list[dict] = []  # Detected indicator usages

    def generate(self, script: Script) -> str:
        """Generate Python code from a Pine Script AST."""
        self._lines = []
        self._indicators = []

        # Header
        self._emit("# Auto-generated from Pine Script by QuantForge transpiler")
        self._emit("from __future__ import annotations")
        self._emit("")

        # Detect strategy info
        strategy_name = "PineStrategy"
        for decl in script.declarations:
            if isinstance(decl, StrategyDecl):
                title = decl.kwargs.get("title")
                if title and isinstance(title, StringLiteral):
                    strategy_name = title.value.replace(" ", "")

        # First pass: collect indicators
        for stmt in script.body:
            self._scan_indicators(stmt)

        # Generate imports
        if self._indicators:
            self._emit("from quantforge.pine.interpreter.builtins.ta import (")
            self._indent_level += 1
            seen = set()
            for ind in self._indicators:
                cls = ind["class"]
                if cls not in seen:
                    self._emit(f"_{cls},")
                    seen.add(cls)
            self._indent_level -= 1
            self._emit(")")
        self._emit("")

        # Generate class
        self._emit(f"class {strategy_name}:")
        self._indent_level += 1
        self._emit('"""Transpiled from Pine Script."""')
        self._emit("")

        # Generate update method
        self._emit("def update(self, bar_index, open, high, low, close, volume):")
        self._indent_level += 1

        if not script.body:
            self._emit("pass")
        else:
            for stmt in script.body:
                self._gen_stmt(stmt)

        self._indent_level -= 1
        self._indent_level -= 1

        return "\n".join(self._lines)

    def _emit(self, line: str) -> None:
        prefix = "    " * self._indent_level
        self._lines.append(f"{prefix}{line}")

    # --- Statement generation ---

    def _gen_stmt(self, node: ASTNode) -> None:
        if isinstance(node, Assignment):
            expr_str = self._gen_expr(node.value)
            if node.op == ":=":
                self._emit(f"{node.target} = {expr_str}")
            elif node.op in ("+=", "-=", "*=", "/="):
                self._emit(f"{node.target} {node.op} {expr_str}")
            else:
                self._emit(f"{node.target} = {expr_str}")

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
            self._emit(self._gen_expr(node))

        else:
            expr = self._gen_expr(node)
            if expr:
                self._emit(expr)

    # --- Expression generation ---

    def _gen_expr(self, node: ASTNode | None) -> str:
        if node is None:
            return "None"
        if isinstance(node, NumberLiteral):
            return repr(node.value)
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
            obj_str = self._gen_expr(node.obj)
            return f"{obj_str}.{node.member}"
        elif isinstance(node, IndexAccess):
            obj_str = self._gen_expr(node.obj)
            idx_str = self._gen_expr(node.index)
            return f"{obj_str}[{idx_str}]"
        elif isinstance(node, BinOp):
            left = self._gen_expr(node.left)
            right = self._gen_expr(node.right)
            op = node.op
            if op == "and":
                op = "and"
            elif op == "or":
                op = "or"
            return f"({left} {op} {right})"
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

        # Map strategy calls
        if func_name in _STRATEGY_MAP:
            py_func = _STRATEGY_MAP[func_name]
            args_str = self._gen_call_args(node)
            return f"{py_func}({args_str})"

        # Map ta calls
        if func_name in _TA_MAP:
            args_str = self._gen_call_args(node)
            return f"{func_name.replace('.', '_')}({args_str})"

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

    # --- Indicator scanning ---

    def _scan_indicators(self, node: ASTNode) -> None:
        if isinstance(node, FunctionCall):
            name = self._resolve_name(node.func)
            if name in _TA_MAP:
                self._indicators.append(
                    {
                        "name": name,
                        "class": _TA_MAP[name],
                    }
                )
        # Recurse into child nodes
        if isinstance(node, Assignment):
            self._scan_indicators(node.value)
        elif isinstance(node, IfExpr):
            self._scan_indicators(node.condition)
            for stmt in node.body:
                self._scan_indicators(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan_indicators(stmt)
        elif isinstance(node, BinOp):
            self._scan_indicators(node.left)
            self._scan_indicators(node.right)


def transpile(script: Script) -> str:
    """Transpile a Pine Script AST to Python strategy code."""
    return PineCodeGen().generate(script)
