"""Execution context for Pine Script interpreter.

Manages variables, series, built-in OHLCV data, bar state, and scope.
"""

from __future__ import annotations

import math
from typing import Any

from quantforge.pine.interpreter.builtins.input_fn import InputManager
from quantforge.pine.interpreter.builtins.math_fn import MATH_FUNCTIONS
from quantforge.pine.interpreter.builtins.strategy import (
    Direction,
    StrategyEngine,
    STRATEGY_PROPERTIES,
)
from quantforge.pine.interpreter.builtins.ta import (
    TaATR,
    TaADX,
    TaBBands,
    TaChange,
    TaCrossover,
    TaCrossunder,
    TaEMA,
    TaHighest,
    TaLowest,
    TaMACD,
    TaRMA,
    TaRSI,
    TaSMA,
    TaStoch,
    TaTR,
)
from quantforge.pine.interpreter.series import PineSeries


class BarState:
    """barstate.* properties."""

    def __init__(self):
        self.isfirst = False
        self.islast = False
        self.ishistory = True
        self.isrealtime = False
        self.isnew = True
        self.isconfirmed = True


class ExecutionContext:
    """Pine Script execution context — manages all state for bar-by-bar execution.

    Provides:
    - OHLCV series (open, high, low, close, volume)
    - Variable scopes (global, local, var-persistent)
    - Built-in function dispatch (ta.*, math.*, strategy.*, input.*)
    - Bar state (bar_index, time, barstate.*)
    """

    def __init__(
        self,
        strategy_engine: StrategyEngine | None = None,
        input_overrides: dict[str, object] | None = None,
    ):
        # OHLCV series
        self.open = PineSeries()
        self.high = PineSeries()
        self.low = PineSeries()
        self.close = PineSeries()
        self.volume = PineSeries()
        self.time = PineSeries()

        # Bar tracking
        self.bar_index = 0
        self.bar_time = 0
        self.barstate = BarState()
        self._total_bars = 0

        # Variable scopes
        self._global_vars: dict[str, Any] = {}
        self._var_persistent: dict[str, Any] = {}  # var-declared variables
        self._local_scopes: list[dict[str, Any]] = []  # function call stack
        self._var_initialized: set[str] = set()  # track which var/varip are initialized

        # Series for user variables (auto-created on assignment)
        self._user_series: dict[str, PineSeries] = {}

        # Strategy engine
        self.strategy = strategy_engine or StrategyEngine()
        self.input_manager = InputManager(input_overrides)

        # Stateful ta.* instances (keyed by unique call-site identifier)
        self._ta_instances: dict[str, Any] = {}

        # User-defined functions
        self._user_functions: dict[str, Any] = {}  # name -> FunctionDef AST node

    # ------------------------------------------------------------------
    # Bar lifecycle
    # ------------------------------------------------------------------

    def begin_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        time_val: int = 0,
        total_bars: int = 0,
    ) -> None:
        """Called at the start of each bar to push new OHLCV values."""
        self.open.push(open_)
        self.high.push(high)
        self.low.push(low)
        self.close.push(close)
        self.volume.push(volume)
        self.time.push(time_val)

        self.bar_time = time_val
        self._total_bars = total_bars

        # Update barstate
        self.barstate.isfirst = self.bar_index == 0
        self.barstate.islast = self.bar_index == total_bars - 1
        self.barstate.ishistory = True
        self.barstate.isnew = True
        self.barstate.isconfirmed = True

        # Update strategy bar
        self.strategy.set_bar(self.bar_index, time_val)

    def end_bar(self) -> None:
        """Called at the end of each bar."""
        self.bar_index += 1

    # ------------------------------------------------------------------
    # Variable access
    # ------------------------------------------------------------------

    def get_var(self, name: str) -> Any:
        """Look up a variable by name, checking scopes in order."""
        # Check local scopes (innermost first)
        for scope in reversed(self._local_scopes):
            if name in scope:
                return scope[name]

        # Check var-persistent
        if name in self._var_persistent:
            return self._var_persistent[name]

        # Check globals
        if name in self._global_vars:
            return self._global_vars[name]

        # Built-in series
        builtin_series = {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "time": self.time,
        }
        if name in builtin_series:
            return builtin_series[name]

        # Derived series
        if name == "hl2":
            hi = self.high.current
            lo = self.low.current
            if hi is not None and lo is not None:
                return (hi + lo) / 2.0
            return None
        if name == "hlc3":
            hi, lo, cl = self.high.current, self.low.current, self.close.current
            if hi is not None and lo is not None and cl is not None:
                return (hi + lo + cl) / 3.0
            return None
        if name == "ohlc4":
            op, hi, lo, cl = (
                self.open.current,
                self.high.current,
                self.low.current,
                self.close.current,
            )
            if all(v is not None for v in (op, hi, lo, cl)):
                return (op + hi + lo + cl) / 4.0
            return None

        # bar_index
        if name == "bar_index":
            return self.bar_index

        # na
        if name == "na":
            return None

        return None

    def set_var(self, name: str, value: Any, qualifier: str = "") -> None:
        """Set a variable in the appropriate scope."""
        if qualifier == "var":
            if name not in self._var_initialized:
                self._var_persistent[name] = value
                self._var_initialized.add(name)
            # var only initializes once; subsequent bars skip
            return

        if qualifier == "varip":
            if name not in self._var_initialized:
                self._var_persistent[name] = value
                self._var_initialized.add(name)
            return

        # If in a local scope, set there
        if self._local_scopes:
            self._local_scopes[-1][name] = value
            return

        # Global scope
        self._global_vars[name] = value

    def update_var(self, name: str, value: Any) -> None:
        """Update an existing variable (:= operator)."""
        # Check local scopes
        for scope in reversed(self._local_scopes):
            if name in scope:
                scope[name] = value
                return

        # Check var-persistent
        if name in self._var_persistent:
            self._var_persistent[name] = value
            return

        # Global
        self._global_vars[name] = value

    def push_scope(self, initial_vars: dict[str, Any] | None = None) -> None:
        """Push a new local scope (function call)."""
        self._local_scopes.append(initial_vars or {})

    def pop_scope(self) -> None:
        """Pop the current local scope."""
        if self._local_scopes:
            self._local_scopes.pop()

    # ------------------------------------------------------------------
    # ta.* function dispatch
    # ------------------------------------------------------------------

    def get_ta_instance(self, key: str, factory):
        """Get or create a stateful ta.* instance by call-site key."""
        if key not in self._ta_instances:
            self._ta_instances[key] = factory()
        return self._ta_instances[key]

    def resolve_ta_call(
        self, func_name: str, args: list, kwargs: dict, call_key: str
    ) -> Any:
        """Resolve and execute a ta.* function call."""
        if func_name == "sma":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaSMA(int(length)))
            return inst.update(_series_val(source))

        if func_name == "ema":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaEMA(int(length)))
            return inst.update(_series_val(source))

        if func_name == "rma":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaRMA(int(length)))
            return inst.update(_series_val(source))

        if func_name == "rsi":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaRSI(int(length)))
            return inst.update(_series_val(source))

        if func_name == "tr":
            inst = self.get_ta_instance(call_key, TaTR)
            return inst.update(self.high.current, self.low.current, self.close.current)

        if func_name == "atr":
            (length,) = _extract_args(args, kwargs, ["length"])
            inst = self.get_ta_instance(call_key, lambda: TaATR(int(length)))
            return inst.update(self.high.current, self.low.current, self.close.current)

        if func_name == "adx":
            di_length = args[0] if args else kwargs.get("di_length", 14)
            adx_length = args[1] if len(args) > 1 else kwargs.get("adx_length", 14)
            inst = self.get_ta_instance(
                call_key, lambda: TaADX(int(di_length), int(adx_length))
            )
            return inst.update(self.high.current, self.low.current, self.close.current)

        if func_name == "macd":
            source = args[0] if args else kwargs.get("source", self.close.current)
            fast = args[1] if len(args) > 1 else kwargs.get("fast", 12)
            slow = args[2] if len(args) > 2 else kwargs.get("slow", 26)
            signal = args[3] if len(args) > 3 else kwargs.get("signal", 9)
            inst = self.get_ta_instance(
                call_key, lambda: TaMACD(int(fast), int(slow), int(signal))
            )
            return inst.update(_series_val(source))

        if func_name in ("bb", "bbands"):
            source = args[0] if args else kwargs.get("source", self.close.current)
            length = args[1] if len(args) > 1 else kwargs.get("length", 20)
            mult = args[2] if len(args) > 2 else kwargs.get("mult", 2.0)
            inst = self.get_ta_instance(
                call_key, lambda: TaBBands(int(length), float(mult))
            )
            return inst.update(_series_val(source))

        if func_name == "stoch":
            close = args[0] if args else kwargs.get("close", self.close.current)
            high = args[1] if len(args) > 1 else kwargs.get("high", self.high.current)
            low = args[2] if len(args) > 2 else kwargs.get("low", self.low.current)
            length = args[3] if len(args) > 3 else kwargs.get("length", 14)
            inst = self.get_ta_instance(call_key, lambda: TaStoch(int(length)))
            return inst.update(_series_val(close), _series_val(high), _series_val(low))

        if func_name == "crossover":
            a, b = _extract_args(args, kwargs, ["a", "b"])
            inst = self.get_ta_instance(call_key, TaCrossover)
            return inst.update(_series_val(a), _series_val(b))

        if func_name == "crossunder":
            a, b = _extract_args(args, kwargs, ["a", "b"])
            inst = self.get_ta_instance(call_key, TaCrossunder)
            return inst.update(_series_val(a), _series_val(b))

        if func_name == "highest":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaHighest(int(length)))
            return inst.update(_series_val(source))

        if func_name == "lowest":
            source, length = _extract_args(args, kwargs, ["source", "length"])
            inst = self.get_ta_instance(call_key, lambda: TaLowest(int(length)))
            return inst.update(_series_val(source))

        if func_name == "change":
            source = args[0] if args else kwargs.get("source")
            length = args[1] if len(args) > 1 else kwargs.get("length", 1)
            inst = self.get_ta_instance(call_key, lambda: TaChange(int(length)))
            return inst.update(_series_val(source))

        raise ValueError(f"Unknown ta function: ta.{func_name}")

    # ------------------------------------------------------------------
    # math.* dispatch
    # ------------------------------------------------------------------

    def resolve_math_call(self, func_name: str, args: list, kwargs: dict) -> Any:
        fn = MATH_FUNCTIONS.get(func_name)
        if fn is None:
            raise ValueError(f"Unknown math function: math.{func_name}")
        resolved_args = [_series_val(a) for a in args]
        return fn(*resolved_args)

    # ------------------------------------------------------------------
    # strategy.* dispatch
    # ------------------------------------------------------------------

    def resolve_strategy_call(self, func_name: str, args: list, kwargs: dict) -> Any:
        if func_name == "entry":
            id_ = args[0] if args else kwargs.get("id", "")
            direction = (
                args[1] if len(args) > 1 else kwargs.get("direction", Direction.LONG)
            )
            qty = args[2] if len(args) > 2 else kwargs.get("qty")
            limit = kwargs.get("limit")
            stop = kwargs.get("stop")
            comment = kwargs.get("comment", "")
            self.strategy.entry(
                id=str(id_),
                direction=direction,
                qty=qty,
                limit=limit,
                stop=stop,
                comment=str(comment),
            )
            return None

        if func_name == "exit":
            id_ = args[0] if args else kwargs.get("id", "")
            from_entry = args[1] if len(args) > 1 else kwargs.get("from_entry", "")
            qty = kwargs.get("qty")
            profit = kwargs.get("profit")
            loss = kwargs.get("loss")
            limit = kwargs.get("limit")
            stop = kwargs.get("stop")
            comment = kwargs.get("comment", "")
            self.strategy.exit(
                id=str(id_),
                from_entry=str(from_entry),
                qty=qty,
                profit=profit,
                loss=loss,
                limit=limit,
                stop=stop,
                comment=str(comment),
            )
            return None

        if func_name == "close":
            id_ = args[0] if args else kwargs.get("id", "")
            comment = kwargs.get("comment", "")
            self.strategy.close(id=str(id_), comment=str(comment))
            return None

        if func_name == "close_all":
            comment = kwargs.get("comment", "")
            self.strategy.close_all(comment=str(comment))
            return None

        raise ValueError(f"Unknown strategy function: strategy.{func_name}")

    def resolve_strategy_property(self, prop_name: str) -> Any:
        """Resolve strategy.long, strategy.short, strategy.position_size, etc."""
        if prop_name in STRATEGY_PROPERTIES:
            return STRATEGY_PROPERTIES[prop_name]
        if prop_name == "position_size":
            return self.strategy.position_size
        if prop_name == "position_avg_price":
            return self.strategy.position_avg_price
        if prop_name == "equity":
            return self.strategy.equity
        if prop_name == "initial_capital":
            return self.strategy.initial_capital
        raise ValueError(f"Unknown strategy property: strategy.{prop_name}")

    # ------------------------------------------------------------------
    # input.* dispatch
    # ------------------------------------------------------------------

    def resolve_input_call(self, func_name: str, args: list, kwargs: dict) -> Any:
        # Convert args to kwargs based on common signature
        if func_name == "int":
            defval = args[0] if args else kwargs.get("defval", 0)
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            return self.input_manager.input_int(
                defval=int(defval),
                title=str(title),
                **{k: v for k, v in kwargs.items() if k not in ("defval", "title")},
            )

        if func_name == "float":
            defval = args[0] if args else kwargs.get("defval", 0.0)
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            return self.input_manager.input_float(
                defval=float(defval),
                title=str(title),
                **{k: v for k, v in kwargs.items() if k not in ("defval", "title")},
            )

        if func_name == "bool":
            defval = args[0] if args else kwargs.get("defval", False)
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            return self.input_manager.input_bool(defval=bool(defval), title=str(title))

        if func_name == "string":
            defval = args[0] if args else kwargs.get("defval", "")
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            return self.input_manager.input_string(defval=str(defval), title=str(title))

        if func_name == "source":
            defval = args[0] if args else kwargs.get("defval", "close")
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            source_name = self.input_manager.input_source(
                defval=str(defval), title=str(title)
            )
            # Return the actual series for source inputs
            return self.get_var(source_name)

        # Bare input() call — treat as input.float
        if func_name == "" or func_name is None:
            defval = args[0] if args else kwargs.get("defval", 0)
            title = kwargs.get("title", "")
            return self.input_manager.input_float(
                defval=float(defval), title=str(title)
            )

        raise ValueError(f"Unknown input function: input.{func_name}")

    # ------------------------------------------------------------------
    # Built-in global functions
    # ------------------------------------------------------------------

    def resolve_builtin_call(self, func_name: str, args: list, kwargs: dict) -> Any:
        """Resolve top-level built-in functions like na(), nz(), str.tostring(), etc."""
        if func_name == "na":
            val = args[0] if args else None
            if val is None:
                return True
            if isinstance(val, float) and math.isnan(val):
                return True
            return False

        if func_name == "nz":
            val = args[0] if args else None
            replacement = args[1] if len(args) > 1 else 0.0
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return replacement
            return val

        if func_name == "fixnan":
            val = args[0] if args else None
            if val is None or (isinstance(val, float) and math.isnan(val)):
                # Should return previous non-na value — simplified
                return 0.0
            return val

        if func_name == "str.tostring":
            val = args[0] if args else ""
            return str(val)

        if func_name == "alert":
            return None  # No-op in backtest

        if func_name == "input":
            return self.resolve_input_call("", args, kwargs)

        if func_name == "strategy":
            # strategy() declaration — configure the engine
            self._configure_strategy(kwargs)
            return None

        return None

    def _configure_strategy(self, kwargs: dict) -> None:
        """Apply strategy() declaration parameters."""
        if "initial_capital" in kwargs:
            self.strategy.initial_capital = float(kwargs["initial_capital"])
        if "commission_value" in kwargs:
            self.strategy.commission = float(kwargs["commission_value"]) / 100.0
        if "slippage" in kwargs:
            self.strategy.slippage = float(kwargs["slippage"])
        if "pyramiding" in kwargs:
            self.strategy.pyramiding = int(kwargs["pyramiding"])
        if "default_qty_type" in kwargs:
            self.strategy.default_qty_type = str(kwargs["default_qty_type"])
        if "default_qty_value" in kwargs:
            self.strategy.default_qty_value = float(kwargs["default_qty_value"])
        if "process_orders_on_close" in kwargs:
            self.strategy.process_orders_on_close = bool(
                kwargs["process_orders_on_close"]
            )
        if "currency" in kwargs:
            self.strategy.currency = str(kwargs["currency"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _series_val(v: Any) -> Any:
    """Extract current value from a PineSeries, or return scalar as-is."""
    if isinstance(v, PineSeries):
        return v.current
    return v


def _extract_args(args: list, kwargs: dict, names: list[str]) -> list:
    """Extract positional/keyword args by name."""
    result = []
    for i, name in enumerate(names):
        if i < len(args):
            result.append(args[i])
        elif name in kwargs:
            result.append(kwargs[name])
        else:
            result.append(None)
    return result
