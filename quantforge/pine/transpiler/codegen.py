"""AST → Python QuantForge Strategy code generator.

Maps Pine Script constructs to equivalent Python code that uses
the QuantForge strategy framework.
"""

from __future__ import annotations

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
    NaLiteral,
    NamespacedId,
    Script,
    SeriesIndex,
    StringLiteral,
    TernaryOp,
    UnaryOp,
    VarDecl,
    WhileLoop,
)


# Mapping of ta.* functions to pandas-ta / custom implementations
_TA_MAP = {
    "sma": "ta.sma({source}, length={length})",
    "ema": "ta.ema({source}, length={length})",
    "rsi": "ta.rsi({source}, length={length})",
    "atr": "ta.atr(high=self.high, low=self.low, close=self.close, length={length})",
    "macd": "ta.macd({source}, fast={fast}, slow={slow}, signal={signal})",
    "bbands": "ta.bbands({source}, length={length}, std={mult})",
    "crossover": "ta.cross({a}, {b}, above=True)",
    "crossunder": "ta.cross({a}, {b}, above=False)",
    "highest": "{source}.rolling({length}).max()",
    "lowest": "{source}.rolling({length}).min()",
    "change": "{source}.diff({length})",
    "tr": "ta.true_range(self.high, self.low, self.close)",
}


class PineTranspiler:
    """Transpile Pine Script AST to Python QuantForge strategy code."""

    def __init__(self):
        self._indent = 0
        self._lines: list[str] = []
        self._inputs: list[tuple[str, str, str]] = []  # (name, type, default)
        self._strategy_config: dict[str, str] = {}
        self._var_decls: list[str] = []  # var-declared variables for __init__

    def transpile(self, script: Script) -> str:
        """Convert AST to Python code string."""
        self._lines = []
        self._inputs = []
        self._var_decls = []
        self._strategy_config = {}
        self._indent = 0

        # First pass: collect inputs and strategy config
        for node in script.body:
            if isinstance(node, FunctionCall):
                name = self._call_name(node)
                if name == "strategy":
                    self._collect_strategy_config(node)
            elif isinstance(node, VarDecl) and isinstance(node.value, FunctionCall):
                cname = self._call_name(node.value)
                if cname and cname.startswith("input."):
                    self._collect_input(node)

        # Generate code
        self._emit_header()
        self._emit_class_def()
        self._emit_init()
        self._emit_on_kline(script)

        return "\n".join(self._lines)

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def _emit_header(self) -> None:
        self._line('"""Auto-generated QuantForge strategy from Pine Script."""')
        self._line("")
        self._line("from __future__ import annotations")
        self._line("")
        self._line("from collections import deque")
        self._line("from dataclasses import dataclass")
        self._line("")
        self._line("import pandas_ta as ta")
        self._line("")
        self._line("from quantforge.schema import Kline, OrderSide")
        self._line("from quantforge.strategy import Strategy")
        self._line("")

    def _emit_class_def(self) -> None:
        title = self._strategy_config.get("title", "PineStrategy")
        # Sanitize class name
        class_name = "".join(c if c.isalnum() else "_" for c in title)
        if not class_name or class_name[0].isdigit():
            class_name = "Pine_" + class_name
        self._line("")
        self._line(f"class {class_name}(Strategy):")
        self._indent += 1
        self._line(f'"""Transpiled from Pine Script: {title}."""')
        self._line("")

    def _emit_init(self) -> None:
        self._line("def __init__(self, **kwargs):")
        self._indent += 1
        self._line("super().__init__(**kwargs)")

        # Emit input parameters
        for name, type_, default in self._inputs:
            self._line(
                f"self.{name} = kwargs.get('{name}', {default})  # input.{type_}"
            )

        # Emit var-declared variables
        for name in self._var_decls:
            self._line(f"self.{name} = None")

        self._line("self._bar_count = 0")
        self._line("")
        self._indent -= 1

    def _emit_on_kline(self, script: Script) -> None:
        self._line("def on_kline(self, kline: Kline) -> None:")
        self._indent += 1
        self._line("# Auto-generated from Pine Script")
        self._line("close = kline.close")
        self._line("open_ = kline.open")
        self._line("high = kline.high")
        self._line("low = kline.low")
        self._line("volume = kline.volume")
        self._line("self._bar_count += 1")
        self._line("")

        # Transpile body (skip strategy() and input declarations)
        for node in script.body:
            if isinstance(node, FunctionCall):
                name = self._call_name(node)
                if name == "strategy":
                    continue
            if isinstance(node, VarDecl) and isinstance(node.value, FunctionCall):
                cname = self._call_name(node.value)
                if cname and cname.startswith("input."):
                    continue
            if isinstance(node, FunctionDef):
                continue  # Functions become separate methods

            code = self._transpile_node(node)
            if code:
                for line in code.split("\n"):
                    self._line(line)

        self._indent -= 1

    # ------------------------------------------------------------------
    # Node transpilation
    # ------------------------------------------------------------------

    def _transpile_node(self, node) -> str:
        if node is None:
            return ""

        if isinstance(node, IntLiteral):
            return str(node.value)
        if isinstance(node, FloatLiteral):
            return str(node.value)
        if isinstance(node, BoolLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return repr(node.value)
        if isinstance(node, NaLiteral):
            return "None"
        if isinstance(node, ColorLiteral):
            return repr(node.value)

        if isinstance(node, Identifier):
            return self._map_identifier(node.name)

        if isinstance(node, NamespacedId):
            return self._map_namespaced(node)

        if isinstance(node, SeriesIndex):
            series = self._transpile_node(node.series)
            offset = self._transpile_node(node.offset)
            return f"{series}[{offset}]"

        if isinstance(node, BinaryOp):
            left = self._transpile_node(node.left)
            right = self._transpile_node(node.right)
            op = node.op
            if op == "and":
                return f"({left} and {right})"
            if op == "or":
                return f"({left} or {right})"
            return f"({left} {op} {right})"

        if isinstance(node, UnaryOp):
            operand = self._transpile_node(node.operand)
            if node.op == "not":
                return f"(not {operand})"
            return f"({node.op}{operand})"

        if isinstance(node, TernaryOp):
            cond = self._transpile_node(node.condition)
            true_e = self._transpile_node(node.true_expr)
            false_e = self._transpile_node(node.false_expr)
            return f"({true_e} if {cond} else {false_e})"

        if isinstance(node, FunctionCall):
            return self._transpile_call(node)

        if isinstance(node, VarDecl):
            return self._transpile_var_decl(node)

        if isinstance(node, Assignment):
            return self._transpile_assignment(node)

        if isinstance(node, IfExpr):
            return self._transpile_if(node)

        if isinstance(node, ForLoop):
            return self._transpile_for(node)

        if isinstance(node, WhileLoop):
            return self._transpile_while(node)

        if isinstance(node, BreakStmt):
            return "break"

        if isinstance(node, ContinueStmt):
            return "continue"

        return ""

    def _transpile_call(self, node: FunctionCall) -> str:
        name = self._call_name(node)
        args = [self._transpile_node(a) for a in node.args]
        kwargs = {k: self._transpile_node(v) for k, v in node.kwargs.items()}

        if isinstance(node.name, NamespacedId):
            ns = node.name.namespace
            member = node.name.member

            if ns == "strategy":
                return self._transpile_strategy_call(member, args, kwargs)

            if ns == "ta":
                return self._transpile_ta_call(member, args, kwargs)

            if ns == "math":
                return self._transpile_math_call(member, args)

        if isinstance(node.name, Identifier):
            fname = node.name.name
            if fname == "na":
                return f"({args[0]} is None)" if args else "None"
            if fname == "nz":
                rep = args[1] if len(args) > 1 else "0"
                return f"({args[0]} if {args[0]} is not None else {rep})"

        # Generic function call
        all_args = args + [f"{k}={v}" for k, v in kwargs.items()]
        return f"{name}({', '.join(all_args)})"

    def _transpile_strategy_call(
        self, member: str, args: list[str], kwargs: dict[str, str]
    ) -> str:
        if member == "entry":
            id_ = args[0] if args else kwargs.get("id", "'Long'")
            direction = (
                args[1] if len(args) > 1 else kwargs.get("direction", "strategy.long")
            )
            # Map direction
            if "long" in str(direction).lower():
                side = "OrderSide.BUY"
            else:
                side = "OrderSide.SELL"
            comment = kwargs.get("comment", "''")
            return f"self.create_order(side={side}, comment={comment})  # Pine: strategy.entry({id_})"

        if member == "close":
            id_ = args[0] if args else kwargs.get("id", "'Long'")
            return f"self.close_position()  # Pine: strategy.close({id_})"

        if member == "close_all":
            return "self.close_position()  # Pine: strategy.close_all()"

        if member == "exit":
            id_ = args[0] if args else kwargs.get("id", "''")
            profit = kwargs.get("profit", "None")
            loss = kwargs.get("loss", "None")
            return f"self.exit_position(profit={profit}, loss={loss})  # Pine: strategy.exit({id_})"

        return f"# strategy.{member}(...)  # TODO: implement"

    def _transpile_ta_call(
        self, member: str, args: list[str], kwargs: dict[str, str]
    ) -> str:
        if member == "sma":
            source = args[0] if args else kwargs.get("source", "close")
            length = args[1] if len(args) > 1 else kwargs.get("length", "14")
            return f"ta.sma(pd.Series([{source}]), length={length}).iloc[-1]"

        if member == "ema":
            source = args[0] if args else kwargs.get("source", "close")
            length = args[1] if len(args) > 1 else kwargs.get("length", "14")
            return f"ta.ema(pd.Series([{source}]), length={length}).iloc[-1]"

        if member == "rsi":
            source = args[0] if args else kwargs.get("source", "close")
            length = args[1] if len(args) > 1 else kwargs.get("length", "14")
            return f"ta.rsi(pd.Series([{source}]), length={length}).iloc[-1]"

        if member == "crossover":
            a = args[0] if args else "a"
            b = args[1] if len(args) > 1 else "b"
            return f"({a} > {b} and self._prev_{a} <= self._prev_{b})"

        if member == "crossunder":
            a = args[0] if args else "a"
            b = args[1] if len(args) > 1 else "b"
            return f"({a} < {b} and self._prev_{a} >= self._prev_{b})"

        # Generic ta call
        all_args = args + [f"{k}={v}" for k, v in kwargs.items()]
        return f"ta.{member}({', '.join(all_args)})"

    def _transpile_math_call(self, member: str, args: list[str]) -> str:
        math_map = {
            "abs": "abs",
            "max": "max",
            "min": "min",
            "round": "round",
            "log": "math.log",
            "sqrt": "math.sqrt",
            "pow": "pow",
            "ceil": "math.ceil",
            "floor": "math.floor",
        }
        py_func = math_map.get(member, f"math.{member}")
        return f"{py_func}({', '.join(args)})"

    def _transpile_var_decl(self, node: VarDecl) -> str:
        value = self._transpile_node(node.value)
        name = self._map_identifier(node.name)
        if node.qualifier == "var":
            self._var_decls.append(node.name)
            return f"if self.{node.name} is None: self.{node.name} = {value}  # var"
        return f"{name} = {value}"

    def _transpile_assignment(self, node: Assignment) -> str:
        value = self._transpile_node(node.value)
        name = self._map_identifier(node.target)
        if node.op == ":=":
            return f"{name} = {value}"
        return f"{name} {node.op} {value}"

    def _transpile_if(self, node: IfExpr) -> str:
        lines = []
        cond = self._transpile_node(node.condition)
        lines.append(f"if {cond}:")
        for stmt in node.then_body:
            code = self._transpile_node(stmt)
            if code:
                lines.append(f"    {code}")

        for elif_cond, elif_body in node.elif_clauses:
            ec = self._transpile_node(elif_cond)
            lines.append(f"elif {ec}:")
            for stmt in elif_body:
                code = self._transpile_node(stmt)
                if code:
                    lines.append(f"    {code}")

        if node.else_body:
            lines.append("else:")
            for stmt in node.else_body:
                code = self._transpile_node(stmt)
                if code:
                    lines.append(f"    {code}")

        return "\n".join(lines)

    def _transpile_for(self, node: ForLoop) -> str:
        start = self._transpile_node(node.start)
        stop = self._transpile_node(node.stop)
        step = self._transpile_node(node.step) if node.step else "1"
        lines = [f"for {node.var} in range({start}, {stop} + 1, {step}):"]
        for stmt in node.body:
            code = self._transpile_node(stmt)
            if code:
                lines.append(f"    {code}")
        return "\n".join(lines)

    def _transpile_while(self, node: WhileLoop) -> str:
        cond = self._transpile_node(node.condition)
        lines = [f"while {cond}:"]
        for stmt in node.body:
            code = self._transpile_node(stmt)
            if code:
                lines.append(f"    {code}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _map_identifier(self, name: str) -> str:
        """Map Pine identifiers to Python equivalents."""
        mapping = {
            "close": "close",
            "open": "open_",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "bar_index": "self._bar_count",
            "na": "None",
        }
        return mapping.get(name, name)

    def _map_namespaced(self, node: NamespacedId) -> str:
        ns = node.namespace
        member = node.member
        if ns == "strategy":
            if member == "long":
                return "OrderSide.BUY"
            if member == "short":
                return "OrderSide.SELL"
            if member == "position_size":
                return "self.position_size"
            if member == "position_avg_price":
                return "self.position_avg_price"
        if ns == "barstate":
            return f"self.barstate.{member}"
        return f"{ns}.{member}"

    def _call_name(self, node: FunctionCall) -> str:
        if isinstance(node.name, Identifier):
            return node.name.name
        if isinstance(node.name, NamespacedId):
            return f"{node.name.namespace}.{node.name.member}"
        return ""

    def _collect_strategy_config(self, node: FunctionCall) -> None:
        for k, v in node.kwargs.items():
            self._strategy_config[k] = self._transpile_node(v)

    def _collect_input(self, node: VarDecl) -> None:
        if isinstance(node.value, FunctionCall):
            call = node.value
            if isinstance(call.name, NamespacedId):
                type_ = call.name.member
                default = call.args[0] if call.args else call.kwargs.get("defval")
                default_str = self._transpile_node(default) if default else "None"
                self._inputs.append((node.name, type_, default_str))

    def _line(self, text: str) -> None:
        prefix = "    " * self._indent
        self._lines.append(f"{prefix}{text}")
