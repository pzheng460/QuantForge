"""End-to-end tests: parse Pine → run on data → verify trades.

Tests the full pipeline: source code → parser → AST → runtime → BacktestResult.
"""

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import BacktestResult, PineRuntime
from quantforge.pine.parser.ast_nodes import (
    Assignment,
    BinOp,
    FunctionCall,
    Identifier,
    IfExpr,
    MemberAccess,
    NumberLiteral,
    Script,
    StrategyDecl,
    StringLiteral,
)
from quantforge.pine.transpiler.codegen import transpile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(close_prices: list[float], spread: float = 2.0) -> list[BarData]:
    """Generate OHLCV bars from close prices."""
    bars = []
    for i, c in enumerate(close_prices):
        bars.append(
            BarData(
                open=c - 0.5,
                high=c + spread,
                low=c - spread,
                close=c,
                volume=1000.0 + i * 10,
            )
        )
    return bars


def _trending_up(n: int = 50, start: float = 100.0, step: float = 1.0) -> list[float]:
    """Generate upward trending prices with small noise."""
    import random

    random.seed(42)
    prices = []
    p = start
    for _ in range(n):
        p += step + random.uniform(-0.3, 0.3)
        prices.append(round(p, 2))
    return prices


def _trending_down_then_up(n: int = 80) -> list[float]:
    """Down for first half, up for second half."""
    import random

    random.seed(123)
    prices = []
    p = 150.0
    for i in range(n):
        if i < n // 2:
            p -= 1.0 + random.uniform(-0.2, 0.2)
        else:
            p += 1.0 + random.uniform(-0.2, 0.2)
        prices.append(round(max(p, 10.0), 2))
    return prices


def _oscillating(
    n: int = 100, center: float = 100.0, amplitude: float = 20.0
) -> list[float]:
    """Oscillating prices for RSI testing."""
    import math as m

    return [round(center + amplitude * m.sin(2 * m.pi * i / 20), 2) for i in range(n)]


# ---------------------------------------------------------------------------
# AST-level integration tests (bypassing parser)
# ---------------------------------------------------------------------------


class TestRuntimeDirect:
    """Test runtime by constructing AST nodes directly."""

    def test_simple_assignment(self):
        """Variable assignment and retrieval."""
        script = Script(
            body=[
                Assignment(target="x", value=NumberLiteral(42.0)),
            ]
        )
        bars = _make_bars([100.0, 101.0, 102.0])
        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        runtime.run(script)
        assert ctx.get_var("x") == 42.0

    def test_ema_crossover_strategy(self):
        """EMA crossover strategy produces trades on down-then-up data."""
        prices = _trending_down_then_up(80)
        bars = _make_bars(prices)

        # Build AST for:
        # strategy("Test")
        # fast = ta.ema(close, 5)
        # slow = ta.ema(close, 20)
        # if ta.crossover(fast, slow) -> entry
        # if ta.crossunder(fast, slow) -> close
        script = Script(
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("Test")})],
            body=[
                Assignment(
                    target="fast_ema",
                    value=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="ema"),
                        args=[Identifier("close"), NumberLiteral(5.0)],
                    ),
                ),
                Assignment(
                    target="slow_ema",
                    value=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="ema"),
                        args=[Identifier("close"), NumberLiteral(20.0)],
                    ),
                ),
                IfExpr(
                    condition=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="crossover"),
                        args=[Identifier("fast_ema"), Identifier("slow_ema")],
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="entry"
                            ),
                            args=[
                                StringLiteral("Long"),
                                MemberAccess(obj=Identifier("strategy"), member="long"),
                            ],
                        ),
                    ],
                ),
                IfExpr(
                    condition=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="crossunder"),
                        args=[Identifier("fast_ema"), Identifier("slow_ema")],
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="close"
                            ),
                            args=[StringLiteral("Long")],
                        ),
                    ],
                ),
            ],
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert isinstance(result, BacktestResult)
        assert result.initial_capital == 100000.0
        # On trending data, EMA crossover should generate at least 1 trade
        assert result.total_trades >= 1

    def test_rsi_strategy(self):
        """RSI strategy: buy oversold, sell overbought."""
        prices = _oscillating(150, center=100.0, amplitude=30.0)
        bars = _make_bars(prices)

        script = Script(
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("RSI")})],
            body=[
                Assignment(
                    target="rsi_val",
                    value=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="rsi"),
                        args=[Identifier("close"), NumberLiteral(14.0)],
                    ),
                ),
                IfExpr(
                    condition=BinOp(
                        op="<",
                        left=Identifier("rsi_val"),
                        right=NumberLiteral(40.0),
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="entry"
                            ),
                            args=[
                                StringLiteral("Long"),
                                MemberAccess(obj=Identifier("strategy"), member="long"),
                            ],
                        ),
                    ],
                ),
                IfExpr(
                    condition=BinOp(
                        op=">",
                        left=Identifier("rsi_val"),
                        right=NumberLiteral(60.0),
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="close"
                            ),
                            args=[StringLiteral("Long")],
                        ),
                    ],
                ),
            ],
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 1

    def test_orders_execute_on_next_bar_open(self):
        """Verify orders placed on bar N execute at bar N+1 open."""
        prices = [100.0, 110.0, 120.0, 130.0, 140.0]
        bars = _make_bars(prices)

        # Strategy: unconditionally enter long on first bar
        script = Script(
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("Test")})],
            body=[
                IfExpr(
                    condition=BinOp(
                        op="==",
                        left=Identifier("bar_index"),
                        right=NumberLiteral(0.0),
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="entry"
                            ),
                            args=[
                                StringLiteral("Long"),
                                MemberAccess(obj=Identifier("strategy"), member="long"),
                            ],
                        ),
                    ],
                ),
                IfExpr(
                    condition=BinOp(
                        op="==",
                        left=Identifier("bar_index"),
                        right=NumberLiteral(2.0),
                    ),
                    body=[
                        FunctionCall(
                            func=MemberAccess(
                                obj=Identifier("strategy"), member="close"
                            ),
                            args=[StringLiteral("Long")],
                        ),
                    ],
                ),
            ],
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        # Entry placed on bar 0, executes on bar 1 open
        assert len(result.trades) >= 1
        trade = result.trades[0]
        assert trade.entry_bar == 1  # Executed on bar 1
        assert trade.entry_price == bars[1].open  # At bar 1's open price

    def test_no_strategy_returns_empty_result(self):
        """Script without strategy() produces empty result."""
        script = Script(body=[Assignment(target="x", value=NumberLiteral(1.0))])
        ctx = ExecutionContext(bars=_make_bars([100.0, 101.0]))
        runtime = PineRuntime(ctx)
        result = runtime.run(script)
        assert result.total_trades == 0

    def test_equity_curve_length(self):
        """Equity curve should have one entry per bar."""
        prices = _trending_up(30)
        bars = _make_bars(prices)
        script = Script(
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("Test")})],
            body=[],
        )
        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)
        assert len(result.equity_curve) == len(prices)


class TestTranspiler:
    """Test code generation from AST."""

    def test_simple_transpile(self):
        script = Script(
            declarations=[StrategyDecl(kwargs={"title": StringLiteral("MyStrat")})],
            body=[
                Assignment(
                    target="fast",
                    value=FunctionCall(
                        func=MemberAccess(obj=Identifier("ta"), member="ema"),
                        args=[Identifier("close"), NumberLiteral(10.0)],
                    ),
                ),
            ],
        )
        code = transpile(script)
        assert "MyStrat" in code
        assert "_EMACalc" in code or "_calc_" in code

    def test_if_stmt_transpile(self):
        script = Script(
            body=[
                IfExpr(
                    condition=BinOp(
                        op=">", left=Identifier("x"), right=NumberLiteral(5.0)
                    ),
                    body=[Assignment(target="y", value=NumberLiteral(1.0))],
                ),
            ],
        )
        code = transpile(script)
        assert "if" in code
        assert "y = " in code


class TestContextFromArrays:
    """Test ExecutionContext.from_arrays."""

    def test_from_arrays(self):
        ctx = ExecutionContext.from_arrays(
            open=[10, 20, 30],
            high=[15, 25, 35],
            low=[5, 15, 25],
            close=[12, 22, 32],
        )
        assert len(ctx.bars) == 3
        assert ctx.bars[0].close == 12
        assert ctx.bars[2].high == 35
