"""
Rolling Optimize (Day-Forward Test) mode.

For each day in the test period:
  1. Train on the previous N days using grid search to find best params.
  2. Test on the next day with those params.
  3. Record daily return, Sharpe, trades, and best params.

Aggregate summary: total return, positive-day ratio, avg daily return,
max consecutive losses, parameter stability (std dev across windows).

Usage (via CLI):
    uv run python -m strategy.backtest -S momentum -X bitget -p 3m -R -L 5
    uv run python -m strategy.backtest -S ema_crossover -X bitget -p 3m -R --train-days 14
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from quantforge.backtest import (
    BacktestConfig,
    GridSearchOptimizer,
    ParameterGrid,
    PerformanceAnalyzer,
    VectorizedBacktest,
)

from strategy.backtest.runner import (
    BacktestRunner,
    _apply_signal_delay,
    _bars_per_day,
    _get_position_size_pct,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_params(params: Dict) -> str:
    """Format params dict as a short inline string (first 3 keys)."""
    if not params:
        return "{}"
    items = list(params.items())[:3]
    inner = ", ".join(f"{k}={v}" for k, v in items)
    suffix = ", ..." if len(params) > 3 else ""
    return "{" + inner + suffix + "}"


def _max_consecutive_losses(returns: List[float]) -> int:
    """Compute maximum streak of consecutive negative returns."""
    max_streak = 0
    current = 0
    for r in returns:
        if r < 0:
            current += 1
            if current > max_streak:
                max_streak = current
        else:
            current = 0
    return max_streak


def _compute_param_stability(daily_results: List[Dict]) -> Dict[str, float]:
    """Compute std dev of each numeric parameter across all windows."""
    param_values: Dict[str, List] = {}
    for day in daily_results:
        for k, v in day.get("best_params", {}).items():
            if isinstance(v, (int, float)):
                param_values.setdefault(k, []).append(float(v))
    return {
        k: round(float(np.std(v)), 4) for k, v in param_values.items() if len(v) > 1
    }


def _slice_funding(
    funding_rates: Optional[pd.DataFrame],
    start_ts,
    end_ts,
) -> Optional[pd.DataFrame]:
    """Return funding rate rows in [start_ts, end_ts], or None if no data."""
    if funding_rates is None or funding_rates.empty:
        return None
    sliced = funding_rates[
        (funding_rates.index >= start_ts) & (funding_rates.index <= end_ts)
    ]
    return sliced if not sliced.empty else None


def _make_window_signal_fn(runner: BacktestRunner, train_funding):
    """
    Return a fresh signal function suitable for GridSearchOptimizer.

    A new signal generator is created per call so each rolling window gets
    independent state.  Signal generation is still sequential (shared gen),
    which matches the existing runner.py pattern and avoids thread-safety
    issues documented in CLAUDE.md.
    """
    base_config = runner.reg.config_cls()
    base_filter = runner.reg.filter_config_cls(
        **(runner.reg.default_filter_kwargs or {})
    )
    gen = runner.reg.signal_generator_cls(base_config, base_filter)
    if hasattr(gen, "funding_rates") and train_funding is not None:
        gen.funding_rates = train_funding

    def signal_fn(df: pd.DataFrame, params: Dict) -> np.ndarray:
        return _apply_signal_delay(gen.generate(df, params))

    return signal_fn


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_rolling_optimize(
    runner: BacktestRunner,
    data: pd.DataFrame,
    funding_rates: Optional[pd.DataFrame] = None,
    train_days: int = 7,
    test_days: int = 1,
) -> Dict[str, Any]:
    """Run rolling optimize (day-forward test).

    Args:
        runner:        BacktestRunner configured with strategy + exchange.
        data:          Full OHLCV DataFrame (DatetimeIndex).
        funding_rates: Optional funding rate DataFrame.
        train_days:    Training window size in calendar days (default 7).
        test_days:     Test window size in calendar days (default 1).

    Returns:
        Summary dict with ``daily_results`` list and aggregate statistics.
    """
    bpd = _bars_per_day(runner.reg.default_interval)
    train_bars = bpd * train_days
    test_bars = bpd * test_days

    total_bars = len(data)
    n_windows = total_bars - train_bars - test_bars + 1

    if n_windows <= 0:
        print(
            f"[rolling-optimize] Insufficient data: need at least "
            f"{train_bars + test_bars} bars "
            f"({train_days + test_days} days), have {total_bars}."
        )
        return {}

    has_funding = funding_rates is not None and not funding_rates.empty
    cost_config = runner.profile.cost_config(use_funding_rate=has_funding)

    # Build ParameterGrid once — shared across all windows (immutable)
    grid = ParameterGrid(**runner.reg.default_grid)
    grid_size = len(grid)

    # Position size from default config
    default_cfg = runner.reg.config_cls()
    psp = _get_position_size_pct(default_cfg)

    print(f"\n{'=' * 70}")
    print("ROLLING OPTIMIZE  (Day-Forward Test)")
    print(f"{'=' * 70}")
    print(f"Strategy  : {runner.reg.display_name}")
    print(f"Exchange  : {runner.profile.name}")
    print(f"Leverage  : {runner.leverage}x")
    print(
        f"Data      : {data.index[0].date()} → {data.index[-1].date()} "
        f"({total_bars} bars)"
    )
    print(f"Train     : {train_days}d  ({train_bars} bars / window)")
    print(f"Test      : {test_days}d  ({test_bars} bars / window)")
    print(f"Windows   : {n_windows}")
    print(f"Grid      : {grid_size} combinations / window")
    print(f"{'=' * 70}")
    print(f"  {'DATE':<12} {'RETURN':>9} {'SHARPE':>8} {'TRADES':>7}  BEST PARAMS")
    print(f"  {'-' * 68}")

    daily_results: List[Dict[str, Any]] = []

    for start_idx in range(0, n_windows, test_bars):
        train_slice = data.iloc[start_idx : start_idx + train_bars]
        test_slice = data.iloc[
            start_idx + train_bars : start_idx + train_bars + test_bars
        ]

        if len(test_slice) == 0:
            break

        test_date = test_slice.index[0].date()

        # ---- 1. Grid search on training data --------------------------------
        train_start_ts = train_slice.index[0]
        train_end_ts = train_slice.index[-1]
        train_funding = _slice_funding(funding_rates, train_start_ts, train_end_ts)

        bt_config_train = BacktestConfig(
            symbol=runner.symbol,
            interval=runner.reg.default_interval,
            start_date=train_slice.index[0].to_pydatetime(),
            end_date=train_slice.index[-1].to_pydatetime(),
            initial_capital=10_000.0,
            exchange=runner.profile.ccxt_id,
            leverage=runner.leverage,
        )

        signal_fn = _make_window_signal_fn(runner, train_funding)

        optimizer = GridSearchOptimizer(
            data=train_slice,
            config=bt_config_train,
            signal_generator=signal_fn,
            cost_config=cost_config,
            position_size_pct=psp,
            n_jobs=runner.n_jobs,
        )
        opt_results = optimizer.optimize(grid, target_metric="sharpe_ratio")

        if not opt_results:
            # No valid combinations — record a flat day and continue
            daily_results.append(
                {
                    "date": str(test_date),
                    "return_pct": 0.0,
                    "sharpe": 0.0,
                    "trades": 0,
                    "best_params": {},
                }
            )
            print(f"  {test_date!s:<12} {'0.000%':>9}  (no valid grid results)")
            continue

        best_params = optimizer.get_best_params(opt_results)

        # ---- 2. Backtest on test data with best params ----------------------
        test_start_ts = test_slice.index[0]
        test_end_ts = test_slice.index[-1]
        test_funding = _slice_funding(funding_rates, test_start_ts, test_end_ts)

        bt_config_test = BacktestConfig(
            symbol=runner.symbol,
            interval=runner.reg.default_interval,
            start_date=test_slice.index[0].to_pydatetime(),
            end_date=test_slice.index[-1].to_pydatetime(),
            initial_capital=10_000.0,
            exchange=runner.profile.ccxt_id,
            leverage=runner.leverage,
        )

        cfg_kw, filt_kw = runner._split_params(best_params)
        test_config = runner.reg.config_cls(**cfg_kw)
        test_filter = (
            runner.reg.filter_config_cls(**filt_kw)
            if filt_kw
            else runner.reg.filter_config_cls(
                **(runner.reg.default_filter_kwargs or {})
            )
        )

        gen_test = runner.reg.signal_generator_cls(test_config, test_filter)
        if hasattr(gen_test, "funding_rates") and test_funding is not None:
            gen_test.funding_rates = test_funding

        signals = gen_test.generate(test_slice, best_params)
        signals = _apply_signal_delay(signals)

        test_psp = _get_position_size_pct(test_config)
        bt = VectorizedBacktest(
            config=bt_config_test,
            cost_config=cost_config,
            position_size_pct=test_psp,
        )
        test_result = bt.run(
            data=test_slice, signals=signals, funding_rates=test_funding
        )

        analyzer = PerformanceAnalyzer(
            equity_curve=test_result.equity_curve,
            trades=test_result.trades,
            initial_capital=bt_config_test.initial_capital,
        )
        metrics = analyzer.calculate_metrics()

        ret = round(metrics["total_return_pct"], 3)
        sharpe = round(metrics.get("sharpe_ratio", 0.0), 3)
        trades = int(metrics.get("total_trades", 0))

        daily_results.append(
            {
                "date": str(test_date),
                "return_pct": ret,
                "sharpe": sharpe,
                "trades": trades,
                "best_params": best_params,
            }
        )

        sign = "+" if ret >= 0 else ""
        print(
            f"  {test_date!s:<12} {sign}{ret:.3f}%{'':<2}"
            f"  {sharpe:>7.2f}"
            f"  {trades:>6}"
            f"  {_format_params(best_params)}"
        )

    if not daily_results:
        print("No rolling windows were completed.")
        return {}

    # ---- Aggregate summary --------------------------------------------------
    returns = [d["return_pct"] for d in daily_results]
    total_days = len(returns)
    positive_days = sum(1 for r in returns if r > 0)

    avg_daily = float(np.mean(returns))
    # Compound total return (assumes $10k capital reset each day — additive approximation)
    total_return = float(np.sum(returns))
    max_loss_streak = _max_consecutive_losses(returns)
    param_stability = _compute_param_stability(daily_results)

    print(f"\n{'=' * 70}")
    print("ROLLING OPTIMIZE SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Days Tested           : {total_days}")
    print(f"  Total Return (sum)    : {total_return:+.2f}%")
    print(
        f"  Positive Days         : {positive_days}/{total_days} "
        f"({positive_days / total_days:.0%})"
    )
    print(f"  Avg Daily Return      : {avg_daily:+.3f}%")
    print(f"  Max Consecutive Losses: {max_loss_streak}")
    if param_stability:
        print("  Parameter Stability (std dev across windows):")
        for param, std in param_stability.items():
            print(f"    {param}: {std:.4f}")
    print(f"{'=' * 70}")

    return {
        "daily_results": daily_results,
        "total_days": total_days,
        "positive_days": positive_days,
        "total_return_pct": round(total_return, 3),
        "avg_daily_return_pct": round(avg_daily, 3),
        "max_consecutive_losses": max_loss_streak,
        "param_stability": param_stability,
    }
