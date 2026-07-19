"""End-to-end tests: parse Pine → run on data → verify trades.

Tests the full pipeline: source code → parser → AST → runtime → BacktestResult.
"""

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.builtins.strategy import Direction
from quantforge.pine.interpreter.runtime import BacktestResult, PineRuntime
from quantforge.pine.parser.parser import parse
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

    def test_strategy_commission_percent_is_scaled_from_percent_value(self):
        """commission_value=1 with strategy.commission.percent means 1%, not 100%."""
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Commission", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000, commission_type=strategy.commission.percent, commission_value=1)
if bar_index == 0
    strategy.entry("long", strategy.long)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.trades[0].pnl == -100.0
        assert result.equity_curve[-1] == 99_800.0

    def test_breakeven_trade_is_not_counted_as_losing_trade(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Breakeven", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 1
    strategy.close("Long")
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].pnl == 0.0
        assert result.winning_trades == 0
        assert result.losing_trades == 0

    def test_strategy_entry_qty_zero_is_not_replaced_by_default_qty(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Zero qty", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("Long", strategy.long, qty=0)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 0

    def test_strategy_close_only_closes_matching_entry_id(self):
        """strategy.close("Long") must not close a live "Short" entry."""
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Close ID", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("Short", strategy.short)
if bar_index == 1
    strategy.close("Long")
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].exit_bar == 3
        assert result.trades[0].comment_exit == "end_of_data"

    def test_strategy_exit_only_closes_matching_from_entry(self):
        """strategy.exit(..., from_entry="Long") must not close a "Short" entry."""
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=120.0, low=80.0, close=100.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Exit ID", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("Short", strategy.short)
if bar_index == 1
    strategy.exit("Long stop", "Long", stop=90)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].exit_bar == 3
        assert result.trades[0].comment_exit == "end_of_data"

    def test_strategy_exit_qty_partially_closes_position(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=120.0, low=100.0, close=110.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Partial exit", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=2)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 1
    strategy.exit("Take half", "Long", limit=110, qty=1)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 2
        assert result.trades[0].qty == 1
        assert result.trades[0].exit_price == 110
        assert result.trades[1].qty == 1
        assert result.trades[1].comment_exit == "end_of_data"

    def test_strategy_exit_qty_zero_does_not_fire_close_callback(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=120.0, low=100.0, close=110.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Zero exit qty", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 1
    strategy.exit("Noop", "Long", limit=110, qty=0)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        runtime.run(script)

        close_fills = []
        ctx = ExecutionContext()
        runtime = PineRuntime(ctx)
        runtime.init_incremental(script)
        runtime.strategy_ctx.set_signal_callbacks(
            on_close_fill=lambda **kwargs: close_fills.append(kwargs)
        )
        for bar in bars:
            runtime.process_bar(bar)
        runtime.finalize()

        assert close_fills == [
            {"direction": "long", "price": 110.0, "qty": 1, "order_id": ""}
        ]

    def test_strategy_exit_queued_while_entry_pending_attaches_after_fill(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=90.0, close=95.0, volume=1.0),
            BarData(open=95.0, high=95.0, low=95.0, close=95.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Bracket", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("Long", strategy.long)
    strategy.exit("Long SL", "Long", stop=95)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].exit_bar == 1
        assert result.trades[0].exit_price == 95.0

    def test_strategy_exit_queued_while_short_entry_pending_uses_buy_stop(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=110.0, low=100.0, close=105.0, volume=1.0),
            BarData(open=105.0, high=105.0, low=105.0, close=105.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Short bracket", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("Short", strategy.short)
    strategy.exit("Short SL", "Short", stop=105)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].exit_bar == 1
        assert result.trades[0].exit_price == 105.0

    def test_strategy_exit_reused_id_reattaches_to_new_pending_entry(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=110.0, low=100.0, close=105.0, volume=1.0),
            BarData(open=105.0, high=105.0, low=105.0, close=105.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Reuse exit id", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 1
    strategy.exit("SL", "Long", stop=80)
if bar_index == 2
    strategy.close("Long")
if bar_index == 3
    strategy.entry("Short", strategy.short)
    strategy.exit("SL", "Short", stop=105)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 2
        assert result.trades[1].direction == Direction.SHORT
        assert result.trades[1].exit_bar == 4
        assert result.trades[1].exit_price == 105.0

    def test_strategy_exit_from_entry_can_close_second_pyramided_entry(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=120.0, low=100.0, close=110.0, volume=1.0),
            BarData(open=110.0, high=110.0, low=110.0, close=110.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Pyramiding exit id", initial_capital=100000, pyramiding=2, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("L1", strategy.long)
if bar_index == 1
    strategy.entry("L2", strategy.long)
if bar_index == 2
    strategy.exit("L2 TP", "L2", limit=110)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 2
        assert result.trades[0].comment_entry == "L2"
        assert result.trades[0].qty == 1
        assert result.trades[0].exit_bar == 3
        assert result.trades[0].exit_price == 110.0
        assert result.trades[1].comment_entry == "L1"
        assert result.trades[1].qty == 1
        assert result.trades[1].comment_exit == "end_of_data"

    def test_strategy_pyramiding_allows_new_entry_after_one_lot_closes(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=120.0, low=100.0, close=110.0, volume=1.0),
            BarData(open=110.0, high=110.0, low=110.0, close=110.0, volume=1.0),
            BarData(open=110.0, high=130.0, low=110.0, close=120.0, volume=1.0),
            BarData(open=120.0, high=120.0, low=120.0, close=120.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Pyramiding slot reuse", initial_capital=100000, pyramiding=2, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("L1", strategy.long)
if bar_index == 1
    strategy.entry("L2", strategy.long)
if bar_index == 2
    strategy.exit("L2 TP", "L2", limit=110)
if bar_index == 4
    strategy.entry("L3", strategy.long)
if bar_index == 5
    strategy.exit("L3 TP", "L3", limit=120)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 3
        assert [t.comment_entry for t in result.trades] == ["L2", "L3", "L1"]
        assert result.trades[1].exit_price == 120.0

    def test_generic_exit_from_old_position_is_cancelled_after_reversal(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=80.0, close=90.0, volume=1.0),
            BarData(open=90.0, high=90.0, low=90.0, close=90.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Stale generic exit", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("Long", strategy.long)
if bar_index == 1
    strategy.exit("Generic SL", stop=95)
if bar_index == 2
    strategy.entry("Short", strategy.short)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 2
        assert result.trades[0].comment_exit == ""
        assert result.trades[1].direction == Direction.SHORT
        assert result.trades[1].comment_exit == "end_of_data"
        assert result.trades[1].exit_bar == 6

    def test_exit_declared_before_pending_entry_attaches_on_fill_bar(self):
        bars = [
            BarData(open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0),
            BarData(open=100.0, high=100.0, low=90.0, close=95.0, volume=1.0),
            BarData(open=95.0, high=95.0, low=95.0, close=95.0, volume=1.0),
        ]
        script = parse(
            """
strategy("Exit before entry", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.exit("Long SL", "Long", stop=95)
    strategy.entry("Long", strategy.long)
"""
        )

        ctx = ExecutionContext(bars=bars)
        runtime = PineRuntime(ctx)
        result = runtime.run(script)

        assert result.total_trades == 1
        assert result.trades[0].exit_bar == 1
        assert result.trades[0].exit_price == 95.0


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
