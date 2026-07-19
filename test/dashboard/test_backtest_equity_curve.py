from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.dashboard.backend import jobs
from apps.dashboard.backend.models import BacktestRequest


class _FakeRuntime:
    def __init__(self, _ctx):
        self._bars = []
        self.strategy_ctx = SimpleNamespace(
            reset_trading_state=self._reset_trading_state
        )
        self._reset_at = None

    def init_incremental(self, _ast):
        pass

    def apply_sizing_override(self, _position_size_usdt, _leverage):
        pass

    def process_bar(self, bar):
        self._bars.append(bar)

    def _reset_trading_state(self):
        self._reset_at = len(self._bars)

    def finalize(self):
        period_len = len(self._bars) - (self._reset_at or 0)
        return SimpleNamespace(
            trades=[],
            initial_capital=100_000.0,
            # Warmup produced a +1% offset, then the formal test period has
            # no strategy activity. The frontend period curve must still
            # start at initial_capital, just like buy-and-hold.
            equity_curve=[101_000.0] * period_len,
        )


def test_backtest_equity_curve_reanchors_after_warmup_without_period_trades(
    monkeypatch,
):
    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    day_ms = 24 * 60 * 60 * 1000
    all_ohlcv = [
        [1767139200000, 0, 0, 0, 100.0, 1],  # 2025-12-31 warmup
        [1767225600000, 0, 0, 0, 200.0, 1],  # 2026-01-01 period start
        [1767312000000, 0, 0, 0, 220.0, 1],
        [1767398400000, 0, 0, 0, 240.0, 1],
    ]

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", _FakeRuntime)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: day_ms // 1000)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("No period trades")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-04",
            warmup_bars=1,
        )
    )

    assert result.total_trades == 0
    assert [pt["strategy"] for pt in result.equity_curve] == [
        100_000.0,
        100_000.0,
        100_000.0,
    ]
    assert [pt["bh"] for pt in result.equity_curve] == [
        100_000.0,
        110_000.0,
        120_000.0,
    ]


def test_backtest_discards_warmup_position_before_period_equity(monkeypatch):
    all_ohlcv = [
        [1767139200000, 100.0, 100.0, 100.0, 100.0, 1],  # warmup
        [1767225600000, 200.0, 200.0, 200.0, 200.0, 1],  # period start
        [1767312000000, 300.0, 300.0, 300.0, 300.0, 1],
    ]
    pine = """
strategy("Warmup only entry", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("warmup-long", strategy.long)
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-03",
            warmup_bars=1,
        )
    )

    assert result.total_trades == 0
    assert [pt["strategy"] for pt in result.equity_curve] == [100_000.0, 100_000.0]
    assert [pt["bh"] for pt in result.equity_curve] == [100_000.0, 150_000.0]


def test_backtest_keeps_period_trade_timestamps_after_warmup_reset(monkeypatch):
    all_ohlcv = [
        [1767139200000, 100.0, 100.0, 100.0, 100.0, 1],  # warmup
        [1767225600000, 200.0, 200.0, 200.0, 200.0, 1],  # entry signal
        [1767312000000, 210.0, 210.0, 210.0, 210.0, 1],  # entry fill
        [1767398400000, 220.0, 220.0, 220.0, 220.0, 1],  # close signal
        [1767484800000, 230.0, 230.0, 230.0, 230.0, 1],  # close fill
    ]
    pine = """
strategy("Period trade", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 1
    strategy.entry("period-long", strategy.long)
if bar_index == 3
    strategy.close("period-long")
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-05",
            warmup_bars=1,
        )
    )

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.entry_time == "2026-01-02T00:00:00+00:00"
    assert trade.exit_time == "2026-01-04T00:00:00+00:00"
    assert result.avg_trade_duration_hours == 48.0


def test_backtest_profit_factor_is_infinite_when_no_losing_trades(monkeypatch):
    all_ohlcv = [
        [1767225600000, 200.0, 200.0, 200.0, 200.0, 1],
        [1767312000000, 210.0, 210.0, 210.0, 210.0, 1],
        [1767398400000, 220.0, 220.0, 220.0, 220.0, 1],
        [1767484800000, 230.0, 230.0, 230.0, 230.0, 1],
    ]
    pine = """
strategy("Winner", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("long", strategy.long)
if bar_index == 2
    strategy.close("long")
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-05",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    assert result.profit_factor is None


def test_backtest_reports_drawdown_duration_and_monthly_returns(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767398400000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767484800000, 100.0, 100.0, 100.0, 100.0, 1],
    ]

    class RuntimeWithDrawdown(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0, 90_000.0, 95_000.0, 105_000.0],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithDrawdown)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Drawdown")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-05",
            warmup_bars=0,
        )
    )

    assert result.max_drawdown_pct == 10.0
    assert result.max_dd_duration_days == 2.0
    assert result.monthly_returns == [
        {"year": 2026, "month": 1, "return": pytest.approx(5.0)}
    ]


def test_backtest_end_of_data_close_uses_next_bar_boundary_time(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],  # entry signal
        [1767312000000, 110.0, 110.0, 110.0, 110.0, 1],  # entry fill
        [1767398400000, 120.0, 120.0, 120.0, 120.0, 1],  # end-of-data close
    ]
    pine = """
strategy("End close", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("long", strategy.long)
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-04",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.entry_time == "2026-01-02T00:00:00+00:00"
    assert trade.exit_time == "2026-01-04T00:00:00+00:00"
    assert trade.bars_held == 2
    assert result.avg_trade_duration_hours == 48.0


def test_backtest_annualized_return_uses_return_intervals_not_point_count(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
    ]

    class RuntimeWithOneDailyReturn(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0, 110_000.0],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithOneDailyReturn)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Annualized")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-03",
            warmup_bars=0,
        )
    )

    expected = ((110_000.0 / 100_000.0) ** 365.25 - 1) * 100
    assert result.annualized_return_pct == pytest.approx(expected)


def test_backtest_trade_amount_uses_recorded_quantity_for_breakeven(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767398400000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767484800000, 100.0, 100.0, 100.0, 100.0, 1],
    ]
    pine = """
strategy("Breakeven", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("long", strategy.long)
if bar_index == 2
    strategy.close("long")
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-05",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    assert result.trades[0].pnl == 0.0
    assert result.trades[0].amount == pytest.approx(100.0)


def test_backtest_breakeven_trade_does_not_count_as_consecutive_loss(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767398400000, 100.0, 100.0, 100.0, 100.0, 1],
    ]
    pine = """
strategy("Breakeven", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)
if bar_index == 0
    strategy.entry("long", strategy.long)
if bar_index == 1
    strategy.close("long")
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-04",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    assert result.trades[0].pnl == 0.0
    assert result.avg_loss == 0.0
    assert result.payoff_ratio == 0.0
    assert result.max_consecutive_losses == 0


def test_backtest_trade_pnl_pct_uses_position_notional(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767398400000, 110.0, 110.0, 110.0, 110.0, 1],
        [1767484800000, 110.0, 110.0, 110.0, 110.0, 1],
    ]
    pine = """
strategy("Trade pct", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000)
if bar_index == 0
    strategy.entry("long", strategy.long)
if bar_index == 1
    strategy.close("long")
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-05",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    assert result.trades[0].amount == pytest.approx(100.0)
    assert result.trades[0].pnl == pytest.approx(1_000.0)
    assert result.trades[0].pnl_pct == pytest.approx(10.0)


def test_backtest_expectancy_does_not_treat_breakeven_as_loss_probability(
    monkeypatch,
):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
    ]

    class RuntimeWithMixedTrades(_FakeRuntime):
        def finalize(self):
            from quantforge.pine.interpreter.builtins.strategy import Direction, Trade

            return SimpleNamespace(
                trades=[
                    Trade(
                        entry_bar=0,
                        entry_price=100.0,
                        exit_bar=0,
                        exit_price=110.0,
                        direction=Direction.LONG,
                        qty=10.0,
                        pnl=100.0,
                    ),
                    Trade(
                        entry_bar=0,
                        entry_price=100.0,
                        exit_bar=0,
                        exit_price=90.0,
                        direction=Direction.LONG,
                        qty=10.0,
                        pnl=-100.0,
                    ),
                    Trade(
                        entry_bar=0,
                        entry_price=100.0,
                        exit_bar=0,
                        exit_price=100.0,
                        direction=Direction.LONG,
                        qty=10.0,
                        pnl=0.0,
                    ),
                ],
                initial_capital=100_000.0,
                equity_curve=[100_000.0],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithMixedTrades)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Expectancy")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-02",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 3
    assert result.win_rate_pct == pytest.approx(100 / 3)
    assert result.expectancy == pytest.approx(0.0)


def test_backtest_monthly_returns_include_cross_month_first_bar_return(monkeypatch):
    all_ohlcv = [
        [1767139200000, 100.0, 100.0, 100.0, 100.0, 1],  # 2025-12-31
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],  # 2026-01-01
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],  # 2026-01-02
    ]

    class RuntimeAcrossMonth(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0, 110_000.0, 121_000.0],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeAcrossMonth)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Monthly")',
            exchange="bitget",
            timeframe="1d",
            start_date="2025-12-31",
            end_date="2026-01-03",
            warmup_bars=0,
        )
    )

    assert result.monthly_returns == [
        {"year": 2025, "month": 12, "return": pytest.approx(0.0)},
        {"year": 2026, "month": 1, "return": pytest.approx(21.0)},
    ]


def test_backtest_final_equity_reflects_end_of_data_exit_commission(monkeypatch):
    all_ohlcv = [
        [1767225600000, 100.0, 100.0, 100.0, 100.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
    ]
    pine = """
strategy("Commission", initial_capital=100000, default_qty_type=strategy.cash, default_qty_value=10000, commission_type=strategy.commission.percent, commission_value=1)
if bar_index == 0
    strategy.entry("long", strategy.long)
"""

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source=pine,
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2026-01-03",
            warmup_bars=0,
        )
    )

    assert result.total_trades == 1
    assert result.final_equity == pytest.approx(99_800.0)
    assert result.total_return_pct == pytest.approx(-0.2)


def test_backtest_rejects_zero_period_start_close_for_buy_and_hold(monkeypatch):
    all_ohlcv = [
        [1767225600000, 0.0, 0.0, 0.0, 0.0, 1],
        [1767312000000, 100.0, 100.0, 100.0, 100.0, 1],
    ]

    class RuntimeWithFlatCurve(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0, 100_000.0],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithFlatCurve)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    with pytest.raises(ValueError, match="Period start close price must be positive"):
        jobs._run_pine_backtest(
            BacktestRequest(
                pine_source='strategy("Zero B&H")',
                exchange="bitget",
                timeframe="1d",
                start_date="2026-01-01",
                end_date="2026-01-03",
                warmup_bars=0,
            )
        )


def test_backtest_rejects_empty_period_after_warmup(monkeypatch):
    all_ohlcv = [
        [1767139200000, 100.0, 100.0, 100.0, 100.0, 1],  # before 2026-01-01
    ]

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", _FakeRuntime)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    with pytest.raises(ValueError, match="No OHLCV data in requested backtest period"):
        jobs._run_pine_backtest(
            BacktestRequest(
                pine_source='strategy("Empty period")',
                exchange="bitget",
                timeframe="1d",
                start_date="2026-01-01",
                end_date="2026-01-02",
                warmup_bars=1,
            )
        )


def test_backtest_equity_curve_downsamples_to_at_most_2000_points(monkeypatch):
    start_ms = 1767225600000
    day_ms = 24 * 60 * 60 * 1000
    all_ohlcv = [
        [start_ms + i * day_ms, 100.0, 100.0, 100.0, 100.0 + i, 1.0]
        for i in range(3999)
    ]

    class RuntimeWithLongCurve(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0 + i for i in range(len(all_ohlcv))],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithLongCurve)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Long curve")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2036-12-31",
            warmup_bars=0,
        )
    )

    expected_last_ts = jobs.datetime.fromtimestamp(
        all_ohlcv[-1][0] / 1000, tz=jobs.timezone.utc
    ).isoformat()
    assert len(result.equity_curve) <= 2000
    assert len(result.drawdown_curve) <= 2000
    assert result.equity_curve[-1]["t"] == expected_last_ts
    assert result.drawdown_curve[-1]["t"] == expected_last_ts


def test_backtest_equity_curve_downsampling_keeps_cap_and_final_point(monkeypatch):
    start_ms = 1767225600000
    day_ms = 24 * 60 * 60 * 1000
    all_ohlcv = [
        [start_ms + i * day_ms, 100.0, 100.0, 100.0, 100.0 + i, 1.0]
        for i in range(4000)
    ]

    class RuntimeWithLongCurve(_FakeRuntime):
        def finalize(self):
            return SimpleNamespace(
                trades=[],
                initial_capital=100_000.0,
                equity_curve=[100_000.0 + i for i in range(len(all_ohlcv))],
            )

    import quantforge.pine.interpreter.runtime as runtime_mod
    import quantforge.pine.parser.parser as parser_mod

    monkeypatch.setattr(jobs, "_fetch_ohlcv", lambda *args, **kwargs: all_ohlcv)
    monkeypatch.setattr(parser_mod, "parse", lambda _source: object())
    monkeypatch.setattr(runtime_mod, "PineRuntime", RuntimeWithLongCurve)
    monkeypatch.setattr(jobs, "timeframe_to_seconds", lambda _tf: 24 * 60 * 60)

    result = jobs._run_pine_backtest(
        BacktestRequest(
            pine_source='strategy("Long curve")',
            exchange="bitget",
            timeframe="1d",
            start_date="2026-01-01",
            end_date="2037-01-01",
            warmup_bars=0,
        )
    )

    expected_last_ts = jobs.datetime.fromtimestamp(
        all_ohlcv[-1][0] / 1000, tz=jobs.timezone.utc
    ).isoformat()
    assert len(result.equity_curve) == 2000
    assert len(result.drawdown_curve) == 2000
    assert result.equity_curve[-1]["t"] == expected_last_ts
    assert result.drawdown_curve[-1]["t"] == expected_last_ts
