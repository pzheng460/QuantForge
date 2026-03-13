"""AST → standalone runnable Python strategy code transpiler.

Converts a parsed Pine Script AST into a self-contained Python script that:
- Computes indicators using the same algorithms as the Pine interpreter
- Tracks positions and trades with next-bar-open execution
- Fetches OHLCV data via ccxt (or accepts a list)
- Can be run directly: python transpiled_strategy.py
"""

from __future__ import annotations

import re

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

# ---------------------------------------------------------------------------
# TA function metadata
# (calc_class, has_source_arg, update_pattern)
# has_source_arg: first Pine arg is the data source; rest are init params
# update_pattern: "source" = .update(source_val), "hlc" = .update(high, low, close)
# ---------------------------------------------------------------------------
_TA_CALC_INFO: dict[str, tuple[str, bool, str]] = {
    "ta.sma": ("_SMACalc", True, "source"),
    "ta.ema": ("_EMACalc", True, "source"),
    "ta.rma": ("_RMACalc", True, "source"),
    "ta.rsi": ("_RSICalc", True, "source"),
    "ta.stdev": ("_StdevCalc", True, "source"),
    "ta.macd": ("_MACDCalc", True, "source"),
    "ta.bb": ("_BBCalc", True, "source"),
    "ta.atr": ("_ATRCalc", False, "hlc"),
    "ta.adx": ("_ADXCalc", False, "hlc"),
    "ta.stoch": ("_StochCalc", False, "hlc"),
}

# Calculator class dependencies
_CALC_DEPS: dict[str, list[str]] = {
    "_ATRCalc": ["_RMACalc"],
    "_ADXCalc": ["_ATRCalc", "_RMACalc"],
    "_MACDCalc": ["_EMACalc"],
    "_StochCalc": ["_SMACalc"],
}

# Strategy constants
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

# Math function mapping
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

# Binary operators needing None-safe wrappers
_SAFE_ARITH = {"+": "_add", "-": "_sub", "*": "_mul", "/": "_div", "%": "_mod"}
_SAFE_CMP = {">": "_gt", "<": "_lt", ">=": "_gte", "<=": "_lte"}

# ---------------------------------------------------------------------------
# Calculator class code templates (matching TradingView exactly)
# ---------------------------------------------------------------------------
_CALC_CODE: dict[str, str] = {}

_CALC_CODE["_SMACalc"] = '''
class _SMACalc:
    """Rolling SMA calculator."""
    def __init__(self, length):
        self.length = length
        self._window = deque(maxlen=length)
    def update(self, value):
        if value is None:
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            return None
        return sum(self._window) / self.length
'''

_CALC_CODE["_EMACalc"] = '''
class _EMACalc:
    """EMA: alpha = 2/(length+1), seed with SMA of first `length` bars."""
    def __init__(self, length):
        self.length = length
        self._alpha = 2.0 / (length + 1)
        self._sum = 0.0
        self._count = 0
        self._prev = None
    def update(self, value):
        if value is None:
            return None
        if self._prev is None:
            self._sum += value
            self._count += 1
            if self._count < self.length:
                return None
            self._prev = self._sum / self.length
            return self._prev
        self._prev = self._alpha * value + (1 - self._alpha) * self._prev
        return self._prev
'''

_CALC_CODE["_RMACalc"] = '''
class _RMACalc:
    """RMA (Wilder smoothing): rma = (prev*(length-1) + value) / length. Seed: SMA."""
    def __init__(self, length):
        self.length = length
        self._sum = 0.0
        self._count = 0
        self._prev = None
    def update(self, value):
        if value is None:
            return None
        if self._prev is None:
            self._sum += value
            self._count += 1
            if self._count < self.length:
                return None
            self._prev = self._sum / self.length
            return self._prev
        self._prev = (self._prev * (self.length - 1) + value) / self.length
        return self._prev
'''

_CALC_CODE["_RSICalc"] = '''
class _RSICalc:
    """RSI using RMA (Wilder smoothing) for avg gain / avg loss."""
    def __init__(self, length):
        self.length = length
        self._prev_close = None
        self._gains = []
        self._losses = []
        self._avg_gain = None
        self._avg_loss = None
        self._count = 0
    def update(self, close):
        if close is None:
            return None
        if self._prev_close is None:
            self._prev_close = close
            return None
        change = close - self._prev_close
        self._prev_close = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._count += 1
        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if self._count < self.length:
                return None
            self._avg_gain = sum(self._gains) / self.length
            self._avg_loss = sum(self._losses) / self.length
        else:
            self._avg_gain = (self._avg_gain * (self.length - 1) + gain) / self.length
            self._avg_loss = (self._avg_loss * (self.length - 1) + loss) / self.length
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - 100.0 / (1.0 + rs)
'''

_CALC_CODE["_ATRCalc"] = '''
class _ATRCalc:
    """ATR using RMA (Wilder smoothing) of True Range."""
    def __init__(self, length):
        self.length = length
        self._rma = _RMACalc(length)
        self._prev_close = None
    def update(self, high, low, close):
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        return self._rma.update(tr)
'''

_CALC_CODE["_ADXCalc"] = '''
class _ADXCalc:
    """ADX using Wilder smoothing (RMA)."""
    def __init__(self, length):
        self.length = length
        self._atr = _ATRCalc(length)
        self._plus_dm_rma = _RMACalc(length)
        self._minus_dm_rma = _RMACalc(length)
        self._adx_rma = _RMACalc(length)
        self._prev_high = None
        self._prev_low = None
    def update(self, high, low, close):
        atr = self._atr.update(high, low, close)
        if self._prev_high is None:
            self._prev_high = high
            self._prev_low = low
            return None
        up_move = high - self._prev_high
        down_move = self._prev_low - low
        self._prev_high = high
        self._prev_low = low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        smooth_plus = self._plus_dm_rma.update(plus_dm)
        smooth_minus = self._minus_dm_rma.update(minus_dm)
        if atr is None or smooth_plus is None or smooth_minus is None or atr == 0:
            return None
        plus_di = 100.0 * smooth_plus / atr
        minus_di = 100.0 * smooth_minus / atr
        di_sum = plus_di + minus_di
        dx = 0.0 if di_sum == 0 else 100.0 * abs(plus_di - minus_di) / di_sum
        return self._adx_rma.update(dx)
'''

_CALC_CODE["_MACDCalc"] = '''
class _MACDCalc:
    """MACD: fast EMA - slow EMA, signal = EMA of MACD line."""
    def __init__(self, fast=12, slow=26, signal=9):
        self._fast_ema = _EMACalc(fast)
        self._slow_ema = _EMACalc(slow)
        self._signal_ema = _EMACalc(signal)
    def update(self, close):
        fast = self._fast_ema.update(close)
        slow = self._slow_ema.update(close)
        if fast is None or slow is None:
            return None, None, None
        macd_line = fast - slow
        signal = self._signal_ema.update(macd_line)
        if signal is None:
            return macd_line, None, None
        return macd_line, signal, macd_line - signal
'''

_CALC_CODE["_BBCalc"] = '''
class _BBCalc:
    """Bollinger Bands: middle = SMA, upper/lower = middle +/- mult*stdev."""
    def __init__(self, length, mult=2.0):
        self.length = length
        self.mult = mult
        self._window = deque(maxlen=length)
    def update(self, close):
        self._window.append(close)
        if len(self._window) < self.length:
            return None, None, None
        middle = sum(self._window) / self.length
        variance = sum((x - middle) ** 2 for x in self._window) / self.length
        import math as _math
        std = _math.sqrt(variance)
        upper = middle + self.mult * std
        lower = middle - self.mult * std
        return upper, middle, lower
'''

_CALC_CODE["_StochCalc"] = '''
class _StochCalc:
    """Stochastic: %K and %D."""
    def __init__(self, k_length=14, k_smooth=1, d_smooth=3):
        self.k_length = k_length
        self._highs = deque(maxlen=k_length)
        self._lows = deque(maxlen=k_length)
        self._k_sma = _SMACalc(k_smooth) if k_smooth > 1 else None
        self._d_sma = _SMACalc(d_smooth)
    def update(self, high, low, close):
        self._highs.append(high)
        self._lows.append(low)
        if len(self._highs) < self.k_length:
            return None, None
        hh = max(self._highs)
        ll = min(self._lows)
        raw_k = 50.0 if hh == ll else 100.0 * (close - ll) / (hh - ll)
        k = self._k_sma.update(raw_k) if self._k_sma else raw_k
        if k is None:
            return None, None
        d = self._d_sma.update(k)
        return k, d
'''

_CALC_CODE["_StdevCalc"] = '''
class _StdevCalc:
    """Rolling population standard deviation."""
    def __init__(self, length):
        self.length = length
        self._window = deque(maxlen=length)
    def update(self, value):
        if value is None:
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            return None
        mean = sum(self._window) / self.length
        variance = sum((x - mean) ** 2 for x in self._window) / self.length
        import math as _math
        return _math.sqrt(variance)
'''

_CALC_CODE["_HighestCalc"] = '''
class _HighestCalc:
    """Rolling highest value."""
    def __init__(self, length):
        self.length = length
        self._window = deque(maxlen=length)
    def update(self, value):
        if value is None:
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            return None
        return max(self._window)
'''

_CALC_CODE["_LowestCalc"] = '''
class _LowestCalc:
    """Rolling lowest value."""
    def __init__(self, length):
        self.length = length
        self._window = deque(maxlen=length)
    def update(self, value):
        if value is None:
            return None
        self._window.append(value)
        if len(self._window) < self.length:
            return None
        return min(self._window)
'''

_CALC_CODE["_ChangeCalc"] = '''
class _ChangeCalc:
    """Change in value over `length` bars."""
    def __init__(self, length=1):
        self.length = length
        self._history = deque(maxlen=length + 1)
    def update(self, value):
        if value is None:
            return None
        self._history.append(value)
        if len(self._history) <= self.length:
            return None
        return self._history[-1] - self._history[0]
'''

# Emit order for calc classes (respects dependencies)
_CALC_EMIT_ORDER = [
    "_SMACalc",
    "_EMACalc",
    "_RMACalc",
    "_RSICalc",
    "_StdevCalc",
    "_ATRCalc",
    "_ADXCalc",
    "_MACDCalc",
    "_BBCalc",
    "_StochCalc",
    "_HighestCalc",
    "_LowestCalc",
    "_ChangeCalc",
]


class PineCodeGen:
    """Transpile Pine AST to a standalone runnable Python script."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._indent = 0
        # Scan state
        self._calc_counter = 0
        self._calc_registry: dict[int, dict] = {}  # id(node) -> calc info
        self._crossover_series: set[str] = set()  # vars needing _prev tracking
        self._used_calc_classes: set[str] = set()
        self._inputs: list[dict] = []
        self._input_names: set[str] = set()  # names of input variables
        self._uses_math = False
        self._strategy_name = "PineStrategy"
        self._initial_capital = 100000.0

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def generate(self, script: Script, pine_source: str = "") -> str:
        """Generate standalone Python code from a Pine Script AST."""
        self._lines = []
        self._indent = 0
        self._calc_counter = 0
        self._calc_registry = {}
        self._crossover_series = set()
        self._used_calc_classes = set()
        self._inputs = []
        self._input_names = set()
        self._uses_math = False

        # Phase 1: extract strategy metadata
        self._extract_metadata(script, pine_source)

        # Phase 2: scan body for dependencies
        for stmt in script.body:
            self._scan(stmt)

        # Phase 3: resolve calc class dependencies
        self._resolve_deps()

        # Phase 4: generate code
        self._emit_header()
        self._emit_helpers()
        self._emit_calc_classes()
        self._emit_tracker()
        self._emit_run(script)
        self._emit_fetch_ohlcv()
        self._emit_main()

        return "\n".join(self._lines)

    # -------------------------------------------------------------------
    # Phase 1: Extract metadata
    # -------------------------------------------------------------------

    def _extract_metadata(self, script: Script, pine_source: str = "") -> None:
        for decl in script.declarations:
            if isinstance(decl, StrategyDecl):
                # Try kwargs first
                title = decl.kwargs.get("title")
                if title and isinstance(title, StringLiteral):
                    raw = title.value.replace(" ", "").replace("-", "_")
                    self._strategy_name = "".join(
                        c for c in raw if c.isalnum() or c == "_"
                    )
                cap = decl.kwargs.get("initial_capital")
                if cap and isinstance(cap, NumberLiteral):
                    self._initial_capital = cap.value

        # Fallback: extract strategy name from Pine source text
        if self._strategy_name == "PineStrategy" and pine_source:
            m = re.search(r'strategy\(\s*"([^"]+)"', pine_source)
            if not m:
                m = re.search(r"strategy\(\s*'([^']+)'", pine_source)
            if m:
                raw = m.group(1).replace(" ", "").replace("-", "_").replace("/", "_")
                self._strategy_name = "".join(c for c in raw if c.isalnum() or c == "_")

    # -------------------------------------------------------------------
    # Phase 2: Scan for dependencies
    # -------------------------------------------------------------------

    def _scan(self, node: ASTNode) -> None:
        """Walk the AST to collect calculator instances, inputs, crossover vars."""
        if isinstance(node, FunctionCall):
            name = self._resolve_name(node.func)

            # Register ta.* calculator
            if name in _TA_CALC_INFO:
                calc_class, _has_src, _pattern = _TA_CALC_INFO[name]
                calc_var = f"_calc_{self._calc_counter}"
                self._calc_counter += 1
                self._calc_registry[id(node)] = {
                    "var": calc_var,
                    "class": calc_class,
                    "func": name,
                    "node": node,
                }
                self._used_calc_classes.add(calc_class)

            # Track crossover/crossunder argument variables
            if name in ("ta.crossover", "ta.crossunder"):
                for arg in node.args:
                    if isinstance(arg, Identifier):
                        self._crossover_series.add(arg.name)

            # Track ta.highest / ta.lowest
            if name == "ta.highest":
                calc_var = f"_calc_{self._calc_counter}"
                self._calc_counter += 1
                self._calc_registry[id(node)] = {
                    "var": calc_var,
                    "class": "_HighestCalc",
                    "func": name,
                    "node": node,
                }
                self._used_calc_classes.add("_HighestCalc")

            if name == "ta.lowest":
                calc_var = f"_calc_{self._calc_counter}"
                self._calc_counter += 1
                self._calc_registry[id(node)] = {
                    "var": calc_var,
                    "class": "_LowestCalc",
                    "func": name,
                    "node": node,
                }
                self._used_calc_classes.add("_LowestCalc")

            if name == "ta.change":
                calc_var = f"_calc_{self._calc_counter}"
                self._calc_counter += 1
                self._calc_registry[id(node)] = {
                    "var": calc_var,
                    "class": "_ChangeCalc",
                    "func": name,
                    "node": node,
                }
                self._used_calc_classes.add("_ChangeCalc")

            if name.startswith("math."):
                self._uses_math = True

            # Recurse into args
            for arg in node.args:
                self._scan(arg)
            for v in node.kwargs.values():
                self._scan(v)

        elif isinstance(node, Assignment):
            # Collect input declarations
            if isinstance(node.value, FunctionCall):
                fname = self._resolve_name(node.value.func)
                if fname.startswith("input."):
                    inp = self._make_input(fname, node.value)
                    if inp:
                        inp["name"] = node.target
                        if not any(i["name"] == node.target for i in self._inputs):
                            self._inputs.append(inp)
                            self._input_names.add(node.target)
            self._scan(node.value)

        elif isinstance(node, TupleAssignment):
            self._scan(node.value)

        elif isinstance(node, IfExpr):
            self._scan(node.condition)
            for stmt in node.body:
                self._scan(stmt)
            for cond, body in node.elseif_clauses:
                self._scan(cond)
                for stmt in body:
                    self._scan(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan(stmt)

        elif isinstance(node, BinOp):
            self._scan(node.left)
            self._scan(node.right)

        elif isinstance(node, UnaryOp):
            self._scan(node.operand)

        elif isinstance(node, TernaryOp):
            self._scan(node.condition)
            self._scan(node.true_expr)
            self._scan(node.false_expr)

        elif isinstance(node, ForLoop):
            for stmt in node.body:
                self._scan(stmt)

        elif isinstance(node, WhileLoop):
            self._scan(node.condition)
            for stmt in node.body:
                self._scan(stmt)

    def _resolve_deps(self) -> None:
        """Add dependency calc classes."""
        added = True
        while added:
            added = False
            for cls in list(self._used_calc_classes):
                for dep in _CALC_DEPS.get(cls, []):
                    if dep not in self._used_calc_classes:
                        self._used_calc_classes.add(dep)
                        added = True

    def _make_input(self, func_name: str, node: FunctionCall) -> dict | None:
        defval = None
        if node.args:
            defval = self._eval_literal(node.args[0])
        if "defval" in node.kwargs:
            defval = self._eval_literal(node.kwargs["defval"])
        # input.int → int default, input.float → float default
        if (
            defval is not None
            and func_name == "input.int"
            and isinstance(defval, float)
        ):
            defval = int(defval)
        return {"name": "", "default": defval}

    @staticmethod
    def _eval_literal(node: ASTNode) -> object:
        if isinstance(node, NumberLiteral):
            return node.value
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, NaLiteral):
            return None
        return None

    # -------------------------------------------------------------------
    # Phase 4: Code generation
    # -------------------------------------------------------------------

    def _emit(self, line: str) -> None:
        prefix = "    " * self._indent
        self._lines.append(f"{prefix}{line}")

    def _emit_raw(self, text: str) -> None:
        """Emit pre-formatted text (no indentation added)."""
        for line in text.split("\n"):
            self._lines.append(line)

    # --- Header ---

    def _emit_header(self) -> None:
        self._emit("# Auto-generated standalone strategy from Pine Script")
        self._emit("# by QuantForge transpiler. Run directly: python this_file.py")
        self._emit("")
        self._emit("from __future__ import annotations")
        self._emit("")
        self._emit("import math")
        self._emit("from collections import deque")
        self._emit("from dataclasses import dataclass, field")
        self._emit("")

    # --- None-safe helpers ---

    def _emit_helpers(self) -> None:
        self._emit("")
        self._emit("# --- None-safe arithmetic (matches Pine's na propagation) ---")
        self._emit("")
        self._emit("")
        self._emit("def _add(a, b):")
        self._emit("    return a + b if a is not None and b is not None else None")
        self._emit("")
        self._emit("")
        self._emit("def _sub(a, b):")
        self._emit("    return a - b if a is not None and b is not None else None")
        self._emit("")
        self._emit("")
        self._emit("def _mul(a, b):")
        self._emit("    return a * b if a is not None and b is not None else None")
        self._emit("")
        self._emit("")
        self._emit("def _div(a, b):")
        self._emit(
            "    return a / b if a is not None and b is not None and b != 0 else None"
        )
        self._emit("")
        self._emit("")
        self._emit("def _mod(a, b):")
        self._emit(
            "    return a % b if a is not None and b is not None and b != 0 else None"
        )
        self._emit("")
        self._emit("")
        self._emit("def _gt(a, b):")
        self._emit("    return a > b if a is not None and b is not None else False")
        self._emit("")
        self._emit("")
        self._emit("def _lt(a, b):")
        self._emit("    return a < b if a is not None and b is not None else False")
        self._emit("")
        self._emit("")
        self._emit("def _gte(a, b):")
        self._emit("    return a >= b if a is not None and b is not None else False")
        self._emit("")
        self._emit("")
        self._emit("def _lte(a, b):")
        self._emit("    return a <= b if a is not None and b is not None else False")
        self._emit("")
        self._emit("")
        self._emit("def _crossover(curr_a, prev_a, curr_b, prev_b):")
        self._emit("    if any(v is None for v in (curr_a, prev_a, curr_b, prev_b)):")
        self._emit("        return False")
        self._emit("    return curr_a > curr_b and prev_a <= prev_b")
        self._emit("")
        self._emit("")
        self._emit("def _crossunder(curr_a, prev_a, curr_b, prev_b):")
        self._emit("    if any(v is None for v in (curr_a, prev_a, curr_b, prev_b)):")
        self._emit("        return False")
        self._emit("    return curr_a < curr_b and prev_a >= prev_b")
        self._emit("")

    # --- Calculator classes ---

    def _emit_calc_classes(self) -> None:
        self._emit("")
        self._emit("# --- TA Calculators (matching TradingView exactly) ---")
        for cls_name in _CALC_EMIT_ORDER:
            if cls_name in self._used_calc_classes:
                self._emit_raw(_CALC_CODE[cls_name])

    # --- Strategy tracker ---

    def _emit_tracker(self) -> None:
        self._emit("")
        self._emit("# --- Strategy Position Tracker ---")
        self._emit("")
        self._emit("")
        self._emit("@dataclass")
        self._emit("class Trade:")
        self._emit('    """A completed trade."""')
        self._emit("    entry_bar: int")
        self._emit("    entry_price: float")
        self._emit("    exit_bar: int")
        self._emit("    exit_price: float")
        self._emit('    direction: str  # "long" or "short"')
        self._emit("    qty: float")
        self._emit("    pnl: float")
        self._emit("")
        self._emit("")
        self._emit("class StrategyTracker:")
        self._emit(
            '    """Tracks positions, orders, trades — matching Pine interpreter semantics."""'
        )
        self._emit("")
        self._indent += 1

        # __init__
        self._emit(
            "def __init__(self, initial_capital=100000.0, default_qty=1.0, commission=0.0, pyramiding=1):"
        )
        self._indent += 1
        self._emit("self.initial_capital = initial_capital")
        self._emit("self.default_qty = default_qty")
        self._emit("self.commission = commission")
        self._emit("self.pyramiding = pyramiding")
        self._emit("self.equity = initial_capital")
        self._emit("self.position_dir = None  # None, 'long', 'short'")
        self._emit("self.position_qty = 0.0")
        self._emit("self.position_entry_price = 0.0")
        self._emit("self.position_entry_bar = 0")
        self._emit("self.pending_orders = []")
        self._emit("self.trades = []")
        self._emit("self.equity_curve = []")
        self._emit("self._entry_count = 0")
        self._indent -= 1
        self._emit("")

        # is_flat property
        self._emit("@property")
        self._emit("def is_flat(self):")
        self._emit("    return self.position_dir is None or self.position_qty == 0.0")
        self._emit("")

        # queue_entry
        self._emit(
            "def queue_entry(self, entry_id, direction, bar_index, qty=None, limit=None, stop=None):"
        )
        self._indent += 1
        self._emit("self.pending_orders.append({")
        self._indent += 1
        self._emit("'id': entry_id, 'direction': direction, 'action': 'entry',")
        self._emit("'qty': qty, 'limit': limit, 'stop': stop, 'bar_index': bar_index,")
        self._indent -= 1
        self._emit("})")
        self._indent -= 1
        self._emit("")

        # queue_close
        self._emit("def queue_close(self, entry_id, bar_index):")
        self._indent += 1
        self._emit("if self.is_flat:")
        self._emit("    return")
        self._emit("opp = 'short' if self.position_dir == 'long' else 'long'")
        self._emit("self.pending_orders.append({")
        self._indent += 1
        self._emit("'id': entry_id or 'close', 'direction': opp, 'action': 'close',")
        self._emit("'qty': None, 'limit': None, 'stop': None, 'bar_index': bar_index,")
        self._indent -= 1
        self._emit("})")
        self._indent -= 1
        self._emit("")

        # queue_close_all
        self._emit("def queue_close_all(self, bar_index):")
        self._emit("    self.queue_close('close_all', bar_index)")
        self._emit("")

        # execute_pending
        self._emit("def execute_pending(self, bar_open, bar_index):")
        self._indent += 1
        self._emit("orders = self.pending_orders")
        self._emit("self.pending_orders = []")
        self._emit("for order in orders:")
        self._indent += 1
        self._emit("fill_price = bar_open")
        # Limit/stop checks
        self._emit("if order['limit'] is not None:")
        self._indent += 1
        self._emit("if order['direction'] == 'long' and fill_price > order['limit']:")
        self._emit("    continue")
        self._emit("if order['direction'] == 'short' and fill_price < order['limit']:")
        self._emit("    continue")
        self._emit("fill_price = order['limit']")
        self._indent -= 1
        self._emit("if order['stop'] is not None:")
        self._indent += 1
        self._emit("if order['direction'] == 'long' and fill_price < order['stop']:")
        self._emit("    continue")
        self._emit("if order['direction'] == 'short' and fill_price > order['stop']:")
        self._emit("    continue")
        self._indent -= 1
        self._emit("if order['action'] == 'entry':")
        self._emit("    self._execute_entry(order, fill_price, bar_index)")
        self._emit("elif order['action'] in ('exit', 'close'):")
        self._emit("    self._execute_close(order, fill_price, bar_index)")
        self._indent -= 1
        self._indent -= 1
        self._emit("")

        # _execute_entry
        self._emit("def _execute_entry(self, order, price, bar_index):")
        self._indent += 1
        self._emit("if not self.is_flat and self.position_dir != order['direction']:")
        self._emit("    self._close_position(price, bar_index)")
        self._emit("if not self.is_flat and self.position_dir == order['direction']:")
        self._emit("    if self._entry_count >= self.pyramiding:")
        self._emit("        return")
        self._emit("qty = order['qty'] or self.default_qty")
        self._emit("comm = qty * price * self.commission")
        self._emit("if self.is_flat:")
        self._indent += 1
        self._emit("self.position_dir = order['direction']")
        self._emit("self.position_qty = qty")
        self._emit("self.position_entry_price = price")
        self._emit("self.position_entry_bar = bar_index")
        self._indent -= 1
        self._emit("else:")
        self._indent += 1
        self._emit("total_qty = self.position_qty + qty")
        self._emit(
            "self.position_entry_price = (self.position_entry_price * self.position_qty + price * qty) / total_qty"
        )
        self._emit("self.position_qty = total_qty")
        self._indent -= 1
        self._emit("self._entry_count += 1")
        self._emit("self.equity -= comm")
        self._indent -= 1
        self._emit("")

        # _execute_close
        self._emit("def _execute_close(self, order, price, bar_index):")
        self._indent += 1
        self._emit("if self.is_flat:")
        self._emit("    return")
        self._emit("qty = order['qty'] if order['qty'] else self.position_qty")
        self._emit("qty = min(qty, self.position_qty)")
        self._emit("self._close_position(price, bar_index, qty)")
        self._indent -= 1
        self._emit("")

        # _close_position
        self._emit("def _close_position(self, price, bar_index, qty=None):")
        self._indent += 1
        self._emit("if self.is_flat:")
        self._emit("    return")
        self._emit("close_qty = qty if qty is not None else self.position_qty")
        self._emit("close_qty = min(close_qty, self.position_qty)")
        self._emit("if self.position_dir == 'long':")
        self._emit("    pnl = (price - self.position_entry_price) * close_qty")
        self._emit("else:")
        self._emit("    pnl = (self.position_entry_price - price) * close_qty")
        self._emit("comm = close_qty * price * self.commission")
        self._emit("pnl -= comm")
        self._emit("self.trades.append(Trade(")
        self._indent += 1
        self._emit(
            "entry_bar=self.position_entry_bar, entry_price=self.position_entry_price,"
        )
        self._emit("exit_bar=bar_index, exit_price=price,")
        self._emit("direction=self.position_dir, qty=close_qty, pnl=pnl,")
        self._indent -= 1
        self._emit("))")
        self._emit("self.equity += pnl")
        self._emit("self.position_qty -= close_qty")
        self._emit("if self.position_qty <= 0:")
        self._indent += 1
        self._emit("self.position_dir = None")
        self._emit("self.position_qty = 0.0")
        self._emit("self.position_entry_price = 0.0")
        self._emit("self._entry_count = 0")
        self._indent -= 1
        self._indent -= 1
        self._emit("")

        # close_remaining
        self._emit("def close_remaining(self, price, bar_index):")
        self._emit("    if not self.is_flat:")
        self._emit("        self._close_position(price, bar_index)")
        self._emit("")

        # update_equity
        self._emit("def update_equity(self, current_price):")
        self._indent += 1
        self._emit("unrealised = 0.0")
        self._emit("if not self.is_flat:")
        self._indent += 1
        self._emit("if self.position_dir == 'long':")
        self._emit(
            "    unrealised = (current_price - self.position_entry_price) * self.position_qty"
        )
        self._emit("else:")
        self._emit(
            "    unrealised = (self.position_entry_price - current_price) * self.position_qty"
        )
        self._indent -= 1
        self._emit("self.equity_curve.append(self.equity + unrealised)")
        self._indent -= 1
        self._emit("")

        # net_profit property
        self._emit("@property")
        self._emit("def net_profit(self):")
        self._emit("    return sum(t.pnl for t in self.trades)")
        self._emit("")

        self._indent -= 1  # end class

    # --- run() function ---

    def _is_constant_assignment(self, node: ASTNode) -> bool:
        """Check if a statement is a constant assignment (literal value, no input/ta call)."""
        if not isinstance(node, Assignment):
            return False
        if isinstance(node.value, FunctionCall):
            return False
        if isinstance(node.value, (NumberLiteral, StringLiteral, BoolLiteral)):
            return True
        return False

    def _emit_run(self, script: Script) -> None:
        self._emit("")
        self._emit(f"STRATEGY_NAME = {self._strategy_name!r}")
        self._emit(f"INITIAL_CAPITAL = {self._initial_capital!r}")
        self._emit("")
        self._emit("")

        # Function signature with input params
        params = ["ohlcv"]
        for inp in self._inputs:
            name = inp["name"]
            default = inp["default"]
            params.append(f"{name}={default!r}")

        self._emit(f"def run({', '.join(params)}):")
        self._indent += 1
        self._emit(
            '"""Run strategy on OHLCV data. ohlcv: list of [timestamp, open, high, low, close, volume]."""'
        )
        self._emit("tracker = StrategyTracker(INITIAL_CAPITAL)")
        self._emit("")

        # Hoist constant assignments before calculator init
        hoisted = set()
        for stmt in script.body:
            if self._is_constant_assignment(stmt):
                self._gen_stmt(stmt)
                hoisted.add(id(stmt))
        if hoisted:
            self._emit("")

        # Initialize calculators
        if self._calc_registry:
            self._emit("# Initialize calculators")
            for calc_info in self._calc_registry.values():
                init_args = self._calc_init_args(calc_info)
                self._emit(f"{calc_info['var']} = {calc_info['class']}({init_args})")
            self._emit("")

        # Prev tracking
        if self._crossover_series:
            self._emit("# Previous values for crossover detection")
            self._emit("_prev = {}")
            self._emit("")

        # Bar loop
        self._emit("for i, bar in enumerate(ohlcv):")
        self._indent += 1
        self._emit(
            "open_, high, low, close, volume = bar[1], bar[2], bar[3], bar[4], bar[5]"
        )
        self._emit("bar_index = i")
        self._emit("")

        # Execute pending orders
        self._emit("# Execute pending orders at this bar's open")
        self._emit("if i > 0:")
        self._emit("    tracker.execute_pending(open_, i)")
        self._emit("")

        # Emit body statements (skip hoisted constants and input assignments)
        self._emit("# --- Strategy logic ---")
        for stmt in script.body:
            if id(stmt) in hoisted:
                continue
            self._gen_stmt(stmt)

        # Update prev values
        if self._crossover_series:
            self._emit("")
            self._emit("# Update previous values for crossover")
            for var_name in sorted(self._crossover_series):
                mapped = self._map_identifier(var_name)
                self._emit(f"_prev[{var_name!r}] = {mapped}")

        # Update equity
        self._emit("")
        self._emit("tracker.update_equity(close)")

        self._indent -= 1  # end for loop
        self._emit("")

        # Close remaining position
        self._emit("# Close any remaining position at end of data")
        self._emit("if ohlcv:")
        self._emit("    tracker.close_remaining(ohlcv[-1][4], len(ohlcv) - 1)")
        self._emit("")
        self._emit("return tracker")

        self._indent -= 1  # end function
        self._emit("")

    # --- fetch_ohlcv() ---

    def _emit_fetch_ohlcv(self) -> None:
        self._emit("")
        self._emit("def fetch_ohlcv(symbol='BTC/USDT:USDT', exchange_id='bitget',")
        self._emit(
            "                timeframe='15m', start='2026-01-01', end='2026-03-12',"
        )
        self._emit("                warmup_days=60):")
        self._indent += 1
        self._emit('"""Fetch OHLCV data from exchange via ccxt."""')
        self._emit("import ccxt")
        self._emit("from datetime import datetime, timezone, timedelta")
        self._emit("")
        self._emit("exchange = getattr(ccxt, exchange_id)({'enableRateLimit': True})")
        self._emit("exchange.load_markets()")
        self._emit("")
        self._emit(
            "start_dt = datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=timezone.utc)"
        )
        self._emit(
            "end_dt = datetime.strptime(end, '%Y-%m-%d').replace(tzinfo=timezone.utc)"
        )
        self._emit("warmup_start = start_dt - timedelta(days=warmup_days)")
        self._emit("since_ms = int(warmup_start.timestamp() * 1000)")
        self._emit("end_ms = int(end_dt.timestamp() * 1000)")
        self._emit("")
        self._emit("all_ohlcv = []")
        self._emit("current_since = since_ms")
        self._emit("while current_since < end_ms:")
        self._indent += 1
        self._emit(
            "batch = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=1000)"
        )
        self._emit("if not batch:")
        self._emit("    break")
        self._emit("all_ohlcv.extend(batch)")
        self._emit("last_ts = batch[-1][0]")
        self._emit("if last_ts <= current_since:")
        self._emit("    break")
        self._emit("current_since = last_ts + 1")
        self._indent -= 1
        self._emit("")
        self._emit("all_ohlcv = [bar for bar in all_ohlcv if bar[0] <= end_ms]")
        self._emit("return all_ohlcv")
        self._indent -= 1
        self._emit("")

    # --- __main__ ---

    def _emit_main(self) -> None:
        self._emit("")
        self._emit('if __name__ == "__main__":')
        self._indent += 1
        self._emit(f"print(f'Strategy: {self._strategy_name}')")
        self._emit("print('Fetching data...')")
        self._emit("ohlcv = fetch_ohlcv()")
        self._emit("print(f'Loaded {len(ohlcv)} bars')")
        self._emit("")
        self._emit("tracker = run(ohlcv)")
        self._emit("")
        self._emit("# Results")
        self._emit("trades = tracker.trades")
        self._emit("total = len(trades)")
        self._emit("wins = sum(1 for t in trades if t.pnl > 0)")
        self._emit("losses = total - wins")
        self._emit("net_pnl = tracker.net_profit")
        self._emit("win_rate = wins / total if total > 0 else 0.0")
        self._emit("")
        self._emit("print()")
        self._emit("print('=' * 60)")
        self._emit(f"print(f'  Strategy: {self._strategy_name}')")
        self._emit("print('=' * 60)")
        self._emit("print(f'  Initial Capital: ${tracker.initial_capital:,.2f}')")
        self._emit("print(f'  Net P&L:         ${net_pnl:,.2f}')")
        self._emit(
            "print(f'  Return:          {net_pnl / tracker.initial_capital:.2%}')"
        )
        self._emit("print(f'  Total Trades:    {total}')")
        self._emit("print(f'  Winning:         {wins}')")
        self._emit("print(f'  Losing:          {losses}')")
        self._emit("print(f'  Win Rate:        {win_rate:.1%}')")
        self._emit("print('-' * 60)")
        self._emit("")
        self._emit("if trades:")
        self._indent += 1
        self._emit(
            "print(f\"  {'#':>4}  {'Dir':>5}  {'Entry':>12}  {'Exit':>12}  {'P&L':>12}\")"
        )
        self._emit("for i, t in enumerate(trades[:50], 1):")
        self._emit("    d = 'LONG' if t.direction == 'long' else 'SHORT'")
        self._emit(
            "    print(f'  {i:>4}  {d:>5}  {t.entry_price:>12.2f}  {t.exit_price:>12.2f}  {t.pnl:>12.2f}')"
        )
        self._emit("if total > 50:")
        self._emit("    print(f'  ... and {total - 50} more trades')")
        self._indent -= 1
        self._emit("")
        self._emit("print('=' * 60)")
        self._indent -= 1
        self._emit("")

    # -------------------------------------------------------------------
    # Calculator init args
    # -------------------------------------------------------------------

    def _calc_init_args(self, calc_info: dict) -> str:
        """Generate the init arguments for a calculator instance."""
        func_name = calc_info["func"]
        node = calc_info["node"]

        if func_name in _TA_CALC_INFO:
            _cls, has_source, _pattern = _TA_CALC_INFO[func_name]
            # Source-based: skip first arg (source), rest are init params
            if has_source:
                param_args = node.args[1:]
            else:
                param_args = list(node.args)

            parts = []
            for arg in param_args:
                parts.append(self._gen_expr(arg))
            for k, v in node.kwargs.items():
                if k not in (
                    "title",
                    "minval",
                    "maxval",
                    "step",
                    "tooltip",
                    "group",
                    "confirm",
                    "inline",
                    "options",
                ):
                    parts.append(f"{k}={self._gen_expr(v)}")
            return ", ".join(parts)

        # Highest/Lowest/Change: first arg is source, second is length
        if func_name in ("ta.highest", "ta.lowest"):
            if len(node.args) > 1:
                return self._gen_expr(node.args[1])
            return "14"

        if func_name == "ta.change":
            if len(node.args) > 1:
                return self._gen_expr(node.args[1])
            return "1"

        return ""

    # -------------------------------------------------------------------
    # Statement generation
    # -------------------------------------------------------------------

    def _gen_stmt(self, node: ASTNode) -> None:
        if isinstance(node, Assignment):
            # Skip input assignments (they become run() parameters)
            if isinstance(node.value, FunctionCall):
                fname = self._resolve_name(node.value.func)
                if fname.startswith("input."):
                    return

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
            self._indent += 1
            if not node.body:
                self._emit("pass")
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent -= 1
            for elseif_cond, elseif_body in node.elseif_clauses:
                self._emit(f"elif {self._gen_expr(elseif_cond)}:")
                self._indent += 1
                if not elseif_body:
                    self._emit("pass")
                for stmt in elseif_body:
                    self._gen_stmt(stmt)
                self._indent -= 1
            if node.else_body:
                self._emit("else:")
                self._indent += 1
                for stmt in node.else_body:
                    self._gen_stmt(stmt)
                self._indent -= 1

        elif isinstance(node, ForLoop):
            start = self._gen_expr(node.start)
            stop = self._gen_expr(node.stop)
            step = self._gen_expr(node.step) if node.step else "1"
            self._emit(f"for {node.var} in range({start}, {stop} + 1, {step}):")
            self._indent += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent -= 1

        elif isinstance(node, WhileLoop):
            self._emit(f"while {self._gen_expr(node.condition)}:")
            self._indent += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent -= 1

        elif isinstance(node, BreakStmt):
            self._emit("break")

        elif isinstance(node, ContinueStmt):
            self._emit("continue")

        elif isinstance(node, FunctionDef):
            params = ", ".join(
                f"{p.name}={self._gen_expr(p.default)}" if p.default else p.name
                for p in node.params
            )
            self._emit(f"def {node.name}({params}):")
            self._indent += 1
            for stmt in node.body:
                self._gen_stmt(stmt)
            self._indent -= 1

        elif isinstance(node, FunctionCall):
            code = self._gen_expr(node)
            if code and code != "None":
                self._emit(code)

        else:
            expr = self._gen_expr(node)
            if expr and expr != "None":
                self._emit(expr)

    # -------------------------------------------------------------------
    # Expression generation
    # -------------------------------------------------------------------

    def _gen_expr(self, node: ASTNode | None) -> str:
        if node is None:
            return "None"

        if isinstance(node, NumberLiteral):
            v = node.value
            return repr(int(v)) if v == int(v) else repr(v)

        if isinstance(node, StringLiteral):
            return repr(node.value)

        if isinstance(node, BoolLiteral):
            return "True" if node.value else "False"

        if isinstance(node, NaLiteral):
            return "None"

        if isinstance(node, ColorLiteral):
            return repr(node.value)

        if isinstance(node, Identifier):
            return self._map_identifier(node.name)

        if isinstance(node, MemberAccess):
            full_name = self._resolve_name(node)
            if full_name in _STRATEGY_CONST_MAP:
                return _STRATEGY_CONST_MAP[full_name]
            obj_str = self._gen_expr(node.obj)
            return f"{obj_str}.{node.member}"

        if isinstance(node, IndexAccess):
            obj_str = self._gen_expr(node.obj)
            idx_str = self._gen_expr(node.index)
            return f"{obj_str}[{idx_str}]"

        if isinstance(node, BinOp):
            left = self._gen_expr(node.left)
            right = self._gen_expr(node.right)
            op = node.op
            if op in _SAFE_ARITH:
                return f"{_SAFE_ARITH[op]}({left}, {right})"
            if op in _SAFE_CMP:
                return f"{_SAFE_CMP[op]}({left}, {right})"
            # Python-native operators for and/or/==/!=
            return f"({left} {op} {right})"

        if isinstance(node, UnaryOp):
            operand = self._gen_expr(node.operand)
            if node.op == "not":
                return f"(not {operand})"
            if node.op == "-":
                return f"(-{operand} if {operand} is not None else None)"
            return f"({node.op}{operand})"

        if isinstance(node, TernaryOp):
            cond = self._gen_expr(node.condition)
            true_e = self._gen_expr(node.true_expr)
            false_e = self._gen_expr(node.false_expr)
            return f"({true_e} if {cond} else {false_e})"

        if isinstance(node, FunctionCall):
            return self._gen_func_call(node)

        return "None"

    def _gen_func_call(self, node: FunctionCall) -> str:
        func_name = self._resolve_name(node.func)

        # No-op functions
        if func_name in _NOOP_FUNCS:
            return f"pass  # Pine: {func_name}(...)"

        # --- Strategy calls ---
        if func_name == "strategy.entry":
            args = self._gen_strategy_entry_args(node)
            return f"tracker.queue_entry({args})"

        if func_name == "strategy.close":
            entry_id = self._gen_expr(node.args[0]) if node.args else "''"
            return f"tracker.queue_close({entry_id}, bar_index)"

        if func_name == "strategy.exit":
            args = self._gen_strategy_exit_args(node)
            return f"tracker.queue_exit({args})"

        if func_name == "strategy.close_all":
            return "tracker.queue_close_all(bar_index)"

        # --- ta.* with registered calculator ---
        if id(node) in self._calc_registry:
            return self._gen_calc_update(node)

        # --- ta.crossover / ta.crossunder ---
        if func_name == "ta.crossover":
            return self._gen_crossover(node, "_crossover")
        if func_name == "ta.crossunder":
            return self._gen_crossover(node, "_crossunder")

        # --- ta.tr (inline) ---
        if func_name == "ta.tr":
            return "None  # ta.tr not yet supported in standalone mode"

        # --- math.* ---
        if func_name in _MATH_MAP:
            py_func = _MATH_MAP[func_name]
            args_str = self._gen_call_args(node)
            return f"{py_func}({args_str})"

        # --- input.* (should not appear in body, handled by skip) ---
        if func_name.startswith("input."):
            # Fallback: return default value
            if node.args:
                return self._gen_expr(node.args[0])
            return "None"

        # --- Built-in helpers ---
        if func_name == "nz":
            val_expr = self._gen_expr(node.args[0]) if node.args else "None"
            repl_expr = self._gen_expr(node.args[1]) if len(node.args) > 1 else "0"
            return f"({val_expr} if {val_expr} is not None else {repl_expr})"

        if func_name == "na":
            if node.args:
                return f"({self._gen_expr(node.args[0])} is None)"
            return "None"

        # General function call
        args_str = self._gen_call_args(node)
        return f"{func_name}({args_str})"

    def _gen_calc_update(self, node: FunctionCall) -> str:
        """Generate calculator .update() call."""
        calc_info = self._calc_registry[id(node)]
        calc_var = calc_info["var"]
        func_name = calc_info["func"]

        # Determine update arguments based on function type
        if func_name in _TA_CALC_INFO:
            _cls, has_source, pattern = _TA_CALC_INFO[func_name]
            if pattern == "hlc":
                return f"{calc_var}.update(high, low, close)"
            # source pattern: first arg is the source
            if node.args:
                source_expr = self._gen_expr(node.args[0])
                return f"{calc_var}.update({source_expr})"
            return f"{calc_var}.update(close)"

        # Highest/Lowest/Change: first arg is source
        if func_name in ("ta.highest", "ta.lowest", "ta.change"):
            if node.args:
                source_expr = self._gen_expr(node.args[0])
                return f"{calc_var}.update({source_expr})"
            return f"{calc_var}.update(close)"

        return f"{calc_var}.update(close)"

    def _gen_crossover(self, node: FunctionCall, helper_name: str) -> str:
        """Generate crossover/crossunder call with prev tracking."""
        if len(node.args) < 2:
            return "False"

        arg_a = node.args[0]
        arg_b = node.args[1]

        # Get current value expression
        curr_a = self._gen_expr(arg_a)
        curr_b = self._gen_expr(arg_b)

        # Get prev value expression
        if isinstance(arg_a, Identifier):
            prev_a = f"_prev.get({arg_a.name!r})"
        else:
            prev_a = "None"

        if isinstance(arg_b, Identifier):
            prev_b = f"_prev.get({arg_b.name!r})"
        else:
            prev_b = "None"

        return f"{helper_name}({curr_a}, {prev_a}, {curr_b}, {prev_b})"

    def _gen_strategy_entry_args(self, node: FunctionCall) -> str:
        """Generate args for tracker.queue_entry()."""
        parts = []
        # entry_id
        if node.args:
            parts.append(self._gen_expr(node.args[0]))
        else:
            parts.append("'entry'")
        # direction
        if len(node.args) > 1:
            parts.append(self._gen_expr(node.args[1]))
        else:
            parts.append(node.kwargs.get("direction", "'long'"))
            if isinstance(parts[-1], ASTNode):
                parts[-1] = self._gen_expr(parts[-1])
        # bar_index
        parts.append("bar_index")
        # qty
        if len(node.args) > 2:
            parts.append(f"qty={self._gen_expr(node.args[2])}")
        elif "qty" in node.kwargs:
            parts.append(f"qty={self._gen_expr(node.kwargs['qty'])}")
        # limit
        if "limit" in node.kwargs:
            parts.append(f"limit={self._gen_expr(node.kwargs['limit'])}")
        # stop
        if "stop" in node.kwargs:
            parts.append(f"stop={self._gen_expr(node.kwargs['stop'])}")
        return ", ".join(parts)

    def _gen_strategy_exit_args(self, node: FunctionCall) -> str:
        """Generate args for tracker.queue_exit()."""
        parts = []
        if node.args:
            parts.append(self._gen_expr(node.args[0]))
        else:
            parts.append("'exit'")
        parts.append("bar_index")
        if "limit" in node.kwargs:
            parts.append(f"limit={self._gen_expr(node.kwargs['limit'])}")
        if "stop" in node.kwargs:
            parts.append(f"stop={self._gen_expr(node.kwargs['stop'])}")
        return ", ".join(parts)

    def _gen_call_args(self, node: FunctionCall) -> str:
        parts = []
        for arg in node.args:
            parts.append(self._gen_expr(arg))
        for k, v in node.kwargs.items():
            parts.append(f"{k}={self._gen_expr(v)}")
        return ", ".join(parts)

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _resolve_name(self, node: ASTNode) -> str:
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, MemberAccess):
            obj = self._resolve_name(node.obj)
            return f"{obj}.{node.member}"
        return ""

    def _map_identifier(self, name: str) -> str:
        mapping = {
            "close": "close",
            "open": "open_",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "bar_index": "bar_index",
            "na": "None",
        }
        return mapping.get(name, name)


def transpile(script: Script, pine_source: str = "") -> str:
    """Transpile a Pine Script AST to standalone Python strategy code."""
    return PineCodeGen().generate(script, pine_source=pine_source)


# ---------------------------------------------------------------------------
# Strategy API transpiler — generates quantforge.dsl.Strategy subclass
# ---------------------------------------------------------------------------

# Maps Pine ta.* calls to (quantforge indicator name, takes_source)
_STRATEGY_API_INDICATOR_MAP: dict[str, tuple[str, bool]] = {
    "ta.ema": ("ema", True),
    "ta.sma": ("sma", True),
    "ta.rsi": ("rsi", True),
    "ta.atr": ("atr", False),
    "ta.adx": ("adx", False),
    "ta.bb": ("bb", True),
    "ta.roc": ("roc", True),
}


class PineStrategyAPICodeGen:
    """Transpile Pine AST to a quantforge.dsl.Strategy subclass.

    Generates compact, readable code using the declarative Strategy API.
    Supports: ta.ema, ta.sma, ta.rsi, ta.atr, ta.adx, ta.bb, ta.roc,
    ta.crossover, ta.crossunder, strategy.entry, strategy.close.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._indent = 0
        self._strategy_name = "PineStrategy"
        self._initial_capital = 100000.0
        self._inputs: list[dict] = []
        self._input_names: set[str] = set()
        self._indicators: list[dict] = []  # {var, type, args, source}
        self._crossover_pairs: list[dict] = []  # {func, arg_a, arg_b, var_name}
        self._has_entry = False
        self._has_close = False
        # Track which assignment target maps to which indicator var
        self._var_to_indicator: dict[str, str] = {}

    def generate(self, script: Script, pine_source: str = "") -> str:
        """Generate a Strategy subclass from Pine AST."""
        self._lines = []
        self._indent = 0

        # Phase 1: extract metadata
        self._extract_metadata(script, pine_source)

        # Phase 2: scan for indicators, inputs, strategy calls
        for stmt in script.body:
            self._scan(stmt)

        # Phase 3: generate Strategy class
        self._emit_code(script)

        return "\n".join(self._lines)

    def _extract_metadata(self, script: Script, pine_source: str = "") -> None:
        for decl in script.declarations:
            if isinstance(decl, StrategyDecl):
                title = decl.kwargs.get("title")
                if title and isinstance(title, StringLiteral):
                    raw = title.value.replace(" ", "_").replace("-", "_")
                    self._strategy_name = "".join(
                        c for c in raw if c.isalnum() or c == "_"
                    )
                cap = decl.kwargs.get("initial_capital")
                if cap and isinstance(cap, NumberLiteral):
                    self._initial_capital = cap.value

        if self._strategy_name == "PineStrategy" and pine_source:
            m = re.search(r'strategy\(\s*"([^"]+)"', pine_source)
            if not m:
                m = re.search(r"strategy\(\s*'([^']+)'", pine_source)
            if m:
                raw = m.group(1).replace(" ", "_").replace("-", "_").replace("/", "_")
                self._strategy_name = "".join(c for c in raw if c.isalnum() or c == "_")

    def _scan(self, node: ASTNode) -> None:
        """Walk AST collecting indicators, inputs, crossovers, strategy calls."""
        if isinstance(node, Assignment):
            if isinstance(node.value, FunctionCall):
                fname = self._resolve_name(node.value.func)

                # Input declarations
                if fname.startswith("input."):
                    inp = self._make_input(fname, node.value)
                    if inp:
                        inp["name"] = node.target
                        if not any(i["name"] == node.target for i in self._inputs):
                            self._inputs.append(inp)
                            self._input_names.add(node.target)
                    return

                # TA indicator assignments
                if fname in _STRATEGY_API_INDICATOR_MAP:
                    ind_name, has_source = _STRATEGY_API_INDICATOR_MAP[fname]
                    args = []
                    param_args = (
                        node.value.args[1:] if has_source else list(node.value.args)
                    )
                    for arg in param_args:
                        args.append(self._eval_literal_or_name(arg))
                    self._indicators.append(
                        {
                            "var": node.target,
                            "type": ind_name,
                            "args": args,
                        }
                    )
                    self._var_to_indicator[node.target] = node.target
                    return

            self._scan(node.value)

        elif isinstance(node, TupleAssignment):
            self._scan(node.value)

        elif isinstance(node, FunctionCall):
            fname = self._resolve_name(node.func)
            if fname == "strategy.entry":
                self._has_entry = True
            elif fname == "strategy.close":
                self._has_close = True

            for arg in node.args:
                self._scan(arg)
            for v in node.kwargs.values():
                self._scan(v)

        elif isinstance(node, IfExpr):
            self._scan(node.condition)
            for stmt in node.body:
                self._scan(stmt)
            for cond, body in node.elseif_clauses:
                self._scan(cond)
                for stmt in body:
                    self._scan(stmt)
            if node.else_body:
                for stmt in node.else_body:
                    self._scan(stmt)

        elif isinstance(node, BinOp):
            self._scan(node.left)
            self._scan(node.right)

        elif isinstance(node, UnaryOp):
            self._scan(node.operand)

        elif isinstance(node, TernaryOp):
            self._scan(node.condition)
            self._scan(node.true_expr)
            self._scan(node.false_expr)

        elif isinstance(node, ForLoop):
            for stmt in node.body:
                self._scan(stmt)

        elif isinstance(node, WhileLoop):
            self._scan(node.condition)
            for stmt in node.body:
                self._scan(stmt)

    def _emit_code(self, script: Script) -> None:
        """Generate the Strategy API class code."""
        # Header
        self._emit(
            '"""Auto-generated Strategy from Pine Script by QuantForge transpiler."""'
        )
        self._emit("")
        self._emit("from quantforge.dsl import Strategy, Param")
        self._emit("")
        self._emit("")

        # Class name: CamelCase from strategy name
        class_name = "".join(
            word.capitalize() for word in self._strategy_name.split("_")
        )
        if not class_name:
            class_name = "PineStrategy"

        self._emit(f"class {class_name}(Strategy):")
        self._indent += 1
        self._emit(f'"""Transpiled from Pine Script: {self._strategy_name}."""')
        self._emit("")

        # Class attributes
        snake_name = self._strategy_name.lower()
        self._emit(f'name = "pine_{snake_name}"')
        self._emit('timeframe = "15m"')
        self._emit("")

        # Params from inputs
        if self._inputs:
            self._emit("# Parameters (from Pine Script inputs)")
            for inp in self._inputs:
                default = inp["default"]
                if default is None:
                    default = 0
                self._emit(f"{inp['name']} = Param({default!r})")
            self._emit("")

        # setup()
        self._emit("def setup(self):")
        self._indent += 1
        if self._indicators:
            for ind in self._indicators:
                args_str = ", ".join(
                    f"self.{a}"
                    if isinstance(a, str) and a in self._input_names
                    else repr(a)
                    for a in ind["args"]
                )
                if args_str:
                    self._emit(
                        f'self.{ind["var"]} = self.add_indicator("{ind["type"]}", {args_str})'
                    )
                else:
                    self._emit(
                        f'self.{ind["var"]} = self.add_indicator("{ind["type"]}")'
                    )
        else:
            self._emit("pass")
        self._indent -= 1
        self._emit("")

        # on_bar()
        self._emit("def on_bar(self, bar):")
        self._indent += 1

        # Check readiness
        if self._indicators:
            ready_checks = " or ".join(
                f"not self.{ind['var']}.ready" for ind in self._indicators
            )
            self._emit(f"if {ready_checks}:")
            self._emit("    return self.HOLD")
            self._emit("")

        # Generate signal logic from body
        self._gen_on_bar_body(script)

        self._emit("")
        self._emit("return self.HOLD")
        self._indent -= 1

        self._indent -= 1  # end class

    def _gen_on_bar_body(self, script: Script) -> None:
        """Generate the on_bar body from Pine strategy logic."""
        for stmt in script.body:
            if isinstance(stmt, Assignment):
                fname = ""
                if isinstance(stmt.value, FunctionCall):
                    fname = self._resolve_name(stmt.value.func)

                # Skip input and indicator assignments (handled in setup)
                if fname.startswith("input.") or fname in _STRATEGY_API_INDICATOR_MAP:
                    continue
                # Skip no-op assignments
                if fname in _NOOP_FUNCS:
                    continue

                # Regular assignments
                expr = self._gen_expr(stmt.value)
                self._emit(f"{stmt.target} = {expr}")

            elif isinstance(stmt, IfExpr):
                self._gen_if_stmt(stmt)

            elif isinstance(stmt, FunctionCall):
                fname = self._resolve_name(stmt.func)
                if fname in _NOOP_FUNCS:
                    continue
                code = self._gen_expr(stmt)
                if code and code != "None":
                    self._emit(code)

    def _gen_if_stmt(self, node: IfExpr) -> None:
        """Generate if statement, mapping strategy.entry/close to return signals."""
        cond = self._gen_expr(node.condition)
        self._emit(f"if {cond}:")
        self._indent += 1
        has_content = False
        for stmt in node.body:
            code = self._gen_body_stmt(stmt)
            if code:
                self._emit(code)
                has_content = True
        if not has_content:
            self._emit("pass")
        self._indent -= 1

        for elseif_cond, elseif_body in node.elseif_clauses:
            self._emit(f"elif {self._gen_expr(elseif_cond)}:")
            self._indent += 1
            has_content = False
            for stmt in elseif_body:
                code = self._gen_body_stmt(stmt)
                if code:
                    self._emit(code)
                    has_content = True
            if not has_content:
                self._emit("pass")
            self._indent -= 1

        if node.else_body:
            self._emit("else:")
            self._indent += 1
            has_content = False
            for stmt in node.else_body:
                code = self._gen_body_stmt(stmt)
                if code:
                    self._emit(code)
                    has_content = True
            if not has_content:
                self._emit("pass")
            self._indent -= 1

    def _gen_body_stmt(self, stmt: ASTNode) -> str | None:
        """Generate a statement inside an if block, mapping strategy calls to return signals."""
        if isinstance(stmt, FunctionCall):
            fname = self._resolve_name(stmt.func)
            if fname == "strategy.entry":
                direction = self._get_entry_direction(stmt)
                if direction == "long":
                    return "return self.BUY"
                else:
                    return "return self.SELL"
            elif fname == "strategy.close":
                return "return self.CLOSE"
            elif fname == "strategy.close_all":
                return "return self.CLOSE"
            elif fname in _NOOP_FUNCS:
                return None
            return self._gen_expr(stmt)

        if isinstance(stmt, IfExpr):
            # Nested if — emit inline
            self._gen_if_stmt(stmt)
            return None

        if isinstance(stmt, Assignment):
            expr = self._gen_expr(stmt.value)
            return f"{stmt.target} = {expr}"

        return None

    def _get_entry_direction(self, node: FunctionCall) -> str:
        """Extract direction from strategy.entry() call."""
        if len(node.args) > 1:
            dir_node = node.args[1]
            if isinstance(dir_node, MemberAccess):
                full = self._resolve_name(dir_node)
                if "short" in full:
                    return "short"
            if isinstance(dir_node, StringLiteral):
                return dir_node.value
        direction = node.kwargs.get("direction")
        if direction:
            if isinstance(direction, MemberAccess):
                full = self._resolve_name(direction)
                if "short" in full:
                    return "short"
            if isinstance(direction, StringLiteral):
                return direction.value
        return "long"

    def _gen_expr(self, node: ASTNode | None) -> str:
        """Generate a Python expression from an AST node."""
        if node is None:
            return "None"

        if isinstance(node, NumberLiteral):
            v = node.value
            return repr(int(v)) if v == int(v) else repr(v)

        if isinstance(node, StringLiteral):
            return repr(node.value)

        if isinstance(node, BoolLiteral):
            return "True" if node.value else "False"

        if isinstance(node, NaLiteral):
            return "None"

        if isinstance(node, ColorLiteral):
            return repr(node.value)

        if isinstance(node, Identifier):
            name = node.name
            # Map indicator variables to self.X.value
            if name in self._var_to_indicator:
                return f"self.{name}.value"
            # Map bar fields
            bar_map = {
                "close": "bar.close",
                "open": "bar.open",
                "high": "bar.high",
                "low": "bar.low",
                "volume": "bar.volume",
            }
            if name in bar_map:
                return bar_map[name]
            # Input params
            if name in self._input_names:
                return f"self.{name}"
            return name

        if isinstance(node, MemberAccess):
            full = self._resolve_name(node)
            if full in _STRATEGY_CONST_MAP:
                return _STRATEGY_CONST_MAP[full]
            obj_str = self._gen_expr(node.obj)
            return f"{obj_str}.{node.member}"

        if isinstance(node, BinOp):
            left = self._gen_expr(node.left)
            right = self._gen_expr(node.right)
            op = node.op
            return f"({left} {op} {right})"

        if isinstance(node, UnaryOp):
            operand = self._gen_expr(node.operand)
            if node.op == "not":
                return f"(not {operand})"
            return f"({node.op}{operand})"

        if isinstance(node, TernaryOp):
            cond = self._gen_expr(node.condition)
            true_e = self._gen_expr(node.true_expr)
            false_e = self._gen_expr(node.false_expr)
            return f"({true_e} if {cond} else {false_e})"

        if isinstance(node, FunctionCall):
            return self._gen_func_expr(node)

        return "None"

    def _gen_func_expr(self, node: FunctionCall) -> str:
        """Generate a function call expression."""
        fname = self._resolve_name(node.func)

        # ta.crossover/crossunder → indicator.crossover(other)
        if fname == "ta.crossover" and len(node.args) >= 2:
            a = self._get_indicator_ref(node.args[0])
            b = self._get_indicator_ref(node.args[1])
            if a and b:
                return f"self.{a}.crossover(self.{b})"
            # Fallback: value comparison
            return f"({self._gen_expr(node.args[0])} > {self._gen_expr(node.args[1])})"

        if fname == "ta.crossunder" and len(node.args) >= 2:
            a = self._get_indicator_ref(node.args[0])
            b = self._get_indicator_ref(node.args[1])
            if a and b:
                return f"self.{a}.crossunder(self.{b})"
            return f"({self._gen_expr(node.args[0])} < {self._gen_expr(node.args[1])})"

        # TA functions that are indicators — use .value
        if fname in _STRATEGY_API_INDICATOR_MAP and id(node) in self._var_to_indicator:
            var = self._var_to_indicator[id(node)]
            return f"self.{var}.value"

        if fname in _NOOP_FUNCS:
            return "None"

        # Built-in helpers
        if fname == "nz":
            val = self._gen_expr(node.args[0]) if node.args else "None"
            repl = self._gen_expr(node.args[1]) if len(node.args) > 1 else "0"
            return f"({val} if {val} is not None else {repl})"

        if fname == "na":
            if node.args:
                return f"({self._gen_expr(node.args[0])} is None)"
            return "None"

        if fname in _MATH_MAP:
            py_func = _MATH_MAP[fname]
            args = ", ".join(self._gen_expr(a) for a in node.args)
            return f"{py_func}({args})"

        # Generic
        args = ", ".join(self._gen_expr(a) for a in node.args)
        return f"{fname}({args})"

    def _get_indicator_ref(self, node: ASTNode) -> str | None:
        """Get indicator variable name from an AST node, if it maps to one."""
        if isinstance(node, Identifier) and node.name in self._var_to_indicator:
            return node.name
        return None

    def _emit(self, line: str) -> None:
        prefix = "    " * self._indent
        self._lines.append(f"{prefix}{line}")

    @staticmethod
    def _resolve_name(node: ASTNode) -> str:
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, MemberAccess):
            obj = PineStrategyAPICodeGen._resolve_name(node.obj)
            return f"{obj}.{node.member}"
        return ""

    def _make_input(self, func_name: str, node: FunctionCall) -> dict | None:
        defval = None
        if node.args:
            defval = self._eval_literal(node.args[0])
        if "defval" in node.kwargs:
            defval = self._eval_literal(node.kwargs["defval"])
        if (
            defval is not None
            and func_name == "input.int"
            and isinstance(defval, float)
        ):
            defval = int(defval)
        return {"name": "", "default": defval}

    @staticmethod
    def _eval_literal(node: ASTNode) -> object:
        if isinstance(node, NumberLiteral):
            return node.value
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, NaLiteral):
            return None
        return None

    def _eval_literal_or_name(self, node: ASTNode) -> object:
        """Evaluate a literal or return identifier name as string."""
        if isinstance(node, NumberLiteral):
            v = node.value
            return int(v) if v == int(v) else v
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        return None


def transpile_strategy_api(script: Script, pine_source: str = "") -> str:
    """Transpile a Pine Script AST to a quantforge.dsl.Strategy subclass."""
    return PineStrategyAPICodeGen().generate(script, pine_source=pine_source)
