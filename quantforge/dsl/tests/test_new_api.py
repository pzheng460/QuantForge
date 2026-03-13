"""Tests for the declarative Strategy API.

Tests:
1. Param grid generation
2. Auto-registration
3. Indicator crossover/crossunder
4. EMA Cross parity with old signal_core
5. All example strategies run without error
6. Backtest produces valid results
"""

from __future__ import annotations

import random

import pytest

from quantforge.dsl.api import Bar, Param, Strategy
from quantforge.dsl.backtest import backtest
from quantforge.dsl.indicators import create_indicator
from quantforge.dsl.registry import get_strategy, list_strategies


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------


def _make_bars(n: int = 500, seed: int = 42) -> list[list]:
    """Generate synthetic OHLCV bars as [ts, open, high, low, close, volume]."""
    rng = random.Random(seed)
    bars = []
    price = 50000.0
    for i in range(n):
        change = rng.gauss(0, 0.005) * price
        o = price
        c = price + change
        h = max(o, c) + abs(rng.gauss(0, 0.002) * price)
        lo = min(o, c) - abs(rng.gauss(0, 0.002) * price)
        v = rng.uniform(100, 10000)
        bars.append([i * 900_000, o, h, lo, c, v])
        price = c
    return bars


def _make_trending_bars(n: int = 300, seed: int = 42) -> list[list]:
    """Generate bars with clear uptrend then downtrend for crossover testing."""
    rng = random.Random(seed)
    bars = []
    price = 50000.0
    for i in range(n):
        # First half: uptrend, second half: downtrend
        if i < n // 2:
            drift = 0.002
        else:
            drift = -0.002
        change = (drift + rng.gauss(0, 0.003)) * price
        o = price
        c = price + change
        h = max(o, c) + abs(rng.gauss(0, 0.001) * price)
        lo = min(o, c) - abs(rng.gauss(0, 0.001) * price)
        v = rng.uniform(100, 10000)
        bars.append([i * 900_000, o, h, lo, c, v])
        price = c
    return bars


# ---------------------------------------------------------------------------
# Test Param
# ---------------------------------------------------------------------------


class TestParam:
    def test_default_value(self):
        p = Param(12)
        assert p.default == 12

    def test_grid_with_min_max_step(self):
        p = Param(12, min=5, max=15, step=5)
        assert p.grid() == [5, 10, 15]

    def test_grid_int_type(self):
        p = Param(12, min=10, max=20, step=5)
        grid = p.grid()
        assert all(isinstance(v, int) for v in grid)

    def test_grid_float_type(self):
        p = Param(0.05, min=0.02, max=0.06, step=0.02)
        grid = p.grid()
        assert all(isinstance(v, float) for v in grid)

    def test_grid_choices(self):
        p = Param("a", choices=["a", "b", "c"])
        assert p.grid() == ["a", "b", "c"]

    def test_grid_default_only(self):
        p = Param(42)
        assert p.grid() == [42]


# ---------------------------------------------------------------------------
# Test auto-registration
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_example_strategies_registered(self):
        import quantforge.dsl.examples  # noqa: F401

        names = list_strategies()
        assert "decl_ema_crossover" in names
        assert "decl_rsi_reversion" in names
        assert "decl_macd_cross" in names
        assert "decl_bb_reversion" in names
        assert "decl_momentum_adx" in names

    def test_get_strategy(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        assert cls.name == "decl_ema_crossover"

    def test_get_strategy_not_found(self):
        with pytest.raises(KeyError, match="not found"):
            get_strategy("nonexistent_strategy_xyz")

    def test_get_param_grid(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        grid = cls.get_param_grid()
        assert "fast_period" in grid
        assert "slow_period" in grid
        assert len(grid["fast_period"]) > 1


# ---------------------------------------------------------------------------
# Test Indicator
# ---------------------------------------------------------------------------


class TestIndicator:
    def test_ema_basic(self):
        ind = create_indicator("ema", 3)
        bar1 = Bar(open=10, high=10, low=10, close=10, volume=100)
        bar2 = Bar(open=11, high=11, low=11, close=11, volume=100)
        bar3 = Bar(open=12, high=12, low=12, close=12, volume=100)
        bar4 = Bar(open=13, high=13, low=13, close=13, volume=100)

        ind._update(bar1)
        assert not ind.ready
        ind._update(bar2)
        assert not ind.ready
        ind._update(bar3)
        assert ind.ready  # period=3, ready after 3 bars
        ind._update(bar4)
        assert ind.value is not None

    def test_crossover(self):
        fast = create_indicator("ema", 3)
        slow = create_indicator("ema", 5)

        # Feed enough data to get both ready
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        for p in prices:
            bar = Bar(open=p, high=p + 1, low=p - 1, close=p, volume=100)
            fast._update(bar)
            slow._update(bar)

        # Both should be ready
        assert fast.ready
        assert slow.ready
        # History should have values
        assert len(fast._history) >= 2

    def test_crossunder(self):
        ind_a = create_indicator("sma", 2)
        ind_b = create_indicator("sma", 2)

        # Manually set up a crossunder scenario
        ind_a._history.appendleft(5.0)  # prev: above
        ind_a._history.appendleft(3.0)  # curr: below
        ind_b._history.appendleft(4.0)  # prev
        ind_b._history.appendleft(4.0)  # curr

        assert ind_a.crossunder(ind_b)
        assert not ind_a.crossover(ind_b)

    def test_lookback(self):
        ind = create_indicator("sma", 2)
        bar1 = Bar(open=10, high=10, low=10, close=10, volume=100)
        bar2 = Bar(open=20, high=20, low=20, close=20, volume=100)
        bar3 = Bar(open=30, high=30, low=30, close=30, volume=100)

        ind._update(bar1)
        ind._update(bar2)
        ind._update(bar3)

        # [0] = most recent (SMA of 20,30 = 25)
        assert ind[0] == 25.0
        # [1] = previous (SMA of 10,20 = 15)
        assert ind[1] == 15.0
        # Out of range
        assert ind[10] is None

    def test_bollinger_bands(self):
        ind = create_indicator("bb", 3, 2.0)
        for p in [100, 101, 102, 103, 104]:
            bar = Bar(open=p, high=p, low=p, close=p, volume=100)
            ind._update(bar)

        assert ind.ready
        assert ind.upper is not None
        assert ind.lower is not None
        assert ind.upper > ind.value
        assert ind.lower < ind.value

    def test_rsi(self):
        ind = create_indicator("rsi", 3)
        prices = [100, 102, 101, 103, 105, 104, 106]
        for p in prices:
            bar = Bar(open=p, high=p, low=p, close=p, volume=100)
            ind._update(bar)

        assert ind.ready
        assert 0 <= ind.value <= 100

    def test_crossed_above_level(self):
        ind = create_indicator("rsi", 3)
        ind._history.appendleft(25.0)  # prev: below 30
        ind._history.appendleft(35.0)  # curr: above 30

        assert ind.crossed_above(30.0)
        assert not ind.crossed_below(30.0)

    def test_atr(self):
        ind = create_indicator("atr", 3)
        data = [
            (100, 105, 95, 100),
            (101, 106, 96, 102),
            (102, 107, 97, 103),
            (103, 108, 98, 104),
            (104, 109, 99, 105),
        ]
        for o, h, lo, c in data:
            bar = Bar(open=o, high=h, low=lo, close=c, volume=100)
            ind._update(bar)

        assert ind.ready
        assert ind.value > 0

    def test_reset(self):
        ind = create_indicator("ema", 3)
        for p in [10, 20, 30, 40]:
            bar = Bar(open=p, high=p, low=p, close=p, volume=100)
            ind._update(bar)
        assert ind.ready
        ind.reset()
        assert not ind.ready
        assert ind.value is None


# ---------------------------------------------------------------------------
# Test Strategy base class
# ---------------------------------------------------------------------------


class TestStrategy:
    def test_param_access(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        strat = cls()
        assert strat.fast_period == 12
        assert strat.slow_period == 26

    def test_param_override(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        strat = cls(fast_period=8, slow_period=21)
        assert strat.fast_period == 8
        assert strat.slow_period == 21

    def test_signal_constants(self):
        assert Strategy.HOLD == 0
        assert Strategy.BUY == 1
        assert Strategy.SELL == -1
        assert Strategy.CLOSE == 2

    def test_indicators_created_in_setup(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        strat = cls()
        assert len(strat._indicators) == 2

    def test_process_bar(self):
        import quantforge.dsl.examples  # noqa: F401

        cls = get_strategy("decl_ema_crossover")
        strat = cls()
        bar = Bar(open=100, high=101, low=99, close=100, volume=1000)
        signal = strat._process_bar(bar)
        assert signal in (0, 1, -1, 2)
        assert strat.bar_index == 1


# ---------------------------------------------------------------------------
# Test EMA Cross parity with old SignalCore
# ---------------------------------------------------------------------------


class TestEMACrossParity:
    """Verify the declarative EMACross produces correct crossover signals
    by comparing against raw StreamingEMA indicator computations."""

    def test_crossover_signals_match(self):
        """Strategy crossover signals should match raw EMA crossover detection."""
        from quantforge.indicators.streaming import StreamingEMA

        fast_p, slow_p = 8, 21
        bars = _make_trending_bars(300, seed=42)

        # Declarative strategy signals
        from quantforge.dsl.examples.ema_cross import EMACross

        strat = EMACross(fast_period=fast_p, slow_period=slow_p)
        strat_signals = []
        for b in bars:
            bar = Bar(open=b[1], high=b[2], low=b[3], close=b[4], volume=b[5])
            sig = strat._process_bar(bar)
            strat_signals.append(sig)

        # Raw streaming EMA crossover detection
        ema_fast = StreamingEMA(fast_p)
        ema_slow = StreamingEMA(slow_p)
        raw_signals = []
        prev_fast = prev_slow = None
        for b in bars:
            close = b[4]
            f = ema_fast.update(close)
            s = ema_slow.update(close)
            sig = 0  # HOLD
            if f is not None and s is not None and prev_fast is not None and prev_slow is not None:
                if prev_fast <= prev_slow and f > s:
                    sig = 1  # BUY
                elif prev_fast >= prev_slow and f < s:
                    sig = -1  # SELL
            prev_fast, prev_slow = f, s
            raw_signals.append(sig)

        # Both should detect crossovers at the same bars
        strat_crosses = [(i, s) for i, s in enumerate(strat_signals) if s in (1, -1)]
        raw_crosses = [(i, s) for i, s in enumerate(raw_signals) if s in (1, -1)]

        assert len(strat_crosses) > 0, "No crossover signals detected (strategy)"
        assert len(raw_crosses) > 0, "No crossover signals detected (raw)"
        assert strat_crosses == raw_crosses, (
            f"Crossover signals differ:\n  strat: {strat_crosses[:10]}\n  raw: {raw_crosses[:10]}"
        )


# ---------------------------------------------------------------------------
# Test Backtest
# ---------------------------------------------------------------------------


class TestBacktest:
    def test_ema_cross_backtest(self):
        from quantforge.dsl.examples.ema_cross import EMACross

        bars = _make_trending_bars(300)
        result = backtest(EMACross, bars, fast_period=8, slow_period=21)

        assert len(result.signals) == 300
        assert len(result.equity_curve) == 300
        assert result.initial_capital == 100_000.0
        assert result.trade_count >= 0

    def test_rsi_backtest(self):
        from quantforge.dsl.examples.rsi_reversion import RSIReversion

        bars = _make_bars(300)
        result = backtest(RSIReversion, bars, rsi_period=7)
        assert len(result.signals) == 300
        assert len(result.equity_curve) == 300

    def test_macd_backtest(self):
        from quantforge.dsl.examples.macd_cross import MACDCross

        bars = _make_trending_bars(300)
        result = backtest(MACDCross, bars)
        assert len(result.signals) == 300

    def test_bb_backtest(self):
        from quantforge.dsl.examples.bb_reversion import BBReversion

        bars = _make_bars(300)
        result = backtest(BBReversion, bars)
        assert len(result.signals) == 300

    def test_momentum_adx_backtest(self):
        from quantforge.dsl.examples.momentum_adx import MomentumADX

        bars = _make_bars(500)
        result = backtest(MomentumADX, bars)
        assert len(result.signals) == 500

    def test_backtest_result_metrics(self):
        from quantforge.dsl.examples.ema_cross import EMACross

        bars = _make_trending_bars(300)
        result = backtest(EMACross, bars)

        assert isinstance(result.total_return_pct, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert result.max_drawdown_pct >= 0
        assert 0 <= result.win_rate <= 1 or result.trade_count == 0

    def test_backtest_with_commission(self):
        from quantforge.dsl.examples.ema_cross import EMACross

        bars = _make_trending_bars(300)
        r1 = backtest(EMACross, bars, commission_pct=0.0)
        r2 = backtest(EMACross, bars, commission_pct=0.001)

        if r1.trade_count > 0:
            assert r2.equity_curve[-1] <= r1.equity_curve[-1]

    def test_short_bars(self):
        from quantforge.dsl.examples.ema_cross import EMACross

        bars = _make_bars(10)
        result = backtest(EMACross, bars)
        assert len(result.signals) == 10


# ---------------------------------------------------------------------------
# Test custom inline strategy
# ---------------------------------------------------------------------------


class TestInlineStrategy:
    def test_minimal_strategy(self):
        class AlwaysBuy(Strategy):
            name = "test_always_buy"

            def on_bar(self, bar):
                return self.BUY

        strat = AlwaysBuy()
        bar = Bar(open=100, high=101, low=99, close=100, volume=100)
        assert strat._process_bar(bar) == 1

    def test_strategy_with_params(self):
        class ParamStrat(Strategy):
            name = "test_param_strat"
            threshold = Param(50, min=10, max=100, step=10)

            def setup(self):
                self.rsi = self.add_indicator("rsi", 14)

            def on_bar(self, bar):
                if self.rsi.ready and self.rsi.value > self.threshold:
                    return self.SELL
                return self.HOLD

        strat = ParamStrat(threshold=70)
        assert strat.threshold == 70
        assert len(strat._indicators) == 1
