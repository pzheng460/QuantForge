"""Optimization jobs for registered Python strategies."""

from __future__ import annotations

import asyncio
import itertools
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from apps.dashboard.backend.jobs.data import (
    _DEFAULT_SYMBOLS,
    _fetch_ohlcv,
    _resolve_date_range,
    timeframe_to_seconds,
)
from apps.dashboard.backend.jobs.registry import (
    JobCancelled,
    _jobs,
    check_cancelled,
)
from apps.dashboard.backend.models import (
    GridRowOut,
    GridSearchResultOut,
    OptimizeRequest,
    ThreeStageResultOut,
    WFOResultOut,
    WFOWindowOut,
)
from quantforge.backtest import BacktestConfig, BacktestResult, run_backtest


def _metrics(result: BacktestResult) -> dict[str, float]:
    curve = result.equity_curve
    returns = [
        curve[i] / curve[i - 1] - 1
        for i in range(1, len(curve))
        if curve[i - 1] > 0
    ]
    mean = sum(returns) / len(returns) if returns else 0
    variance = (
        sum((value - mean) ** 2 for value in returns) / len(returns)
        if returns
        else 0
    )
    sharpe = mean / math.sqrt(variance) * math.sqrt(252) if variance else 0
    peak = result.initial_capital
    drawdown = 0.0
    for equity in curve:
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak if peak else 0)
    total_return = (
        curve[-1] / result.initial_capital - 1 if curve else 0
    )
    wins = sum(trade.pnl > 0 for trade in result.trades)
    return {
        "sharpe": sharpe,
        "return": total_return,
        "drawdown": drawdown,
        "trades": len(result.trades),
        "win_rate": wins / len(result.trades) if result.trades else 0,
    }


def _parameter_grid(strategy_cls: type, limit: int = 500) -> list[dict[str, Any]]:
    schema = strategy_cls.schema()
    names = []
    values = []
    for name, spec in schema.get("properties", {}).items():
        default = spec.get("default")
        if default is None:
            continue
        candidates = [default]
        minimum = spec.get("minimum", spec.get("exclusiveMinimum"))
        maximum = spec.get("maximum", spec.get("exclusiveMaximum"))
        if isinstance(default, (int, float)) and minimum is not None and maximum is not None:
            midpoint = (minimum + maximum) / 2
            candidates.extend([minimum, midpoint, maximum])
            if isinstance(default, int):
                candidates = [int(round(value)) for value in candidates]
        names.append(name)
        values.append(list(dict.fromkeys(candidates)))
    grid = [dict(zip(names, combo)) for combo in itertools.product(*values)]
    if len(grid) > limit:
        stride = max(1, len(grid) // limit)
        grid = grid[::stride][:limit]
    return grid or [{}]


def _load_data(req: OptimizeRequest) -> tuple[list[list], int, str, str]:
    start, end = _resolve_date_range(req.period, req.start_date, req.end_date)
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(
        seconds=timeframe_to_seconds(req.timeframe) * req.warmup_bars
    )
    symbol = req.symbol or _DEFAULT_SYMBOLS[req.exchange]
    rows = _fetch_ohlcv(
        req.exchange,
        symbol,
        req.timeframe,
        int(warmup_start.timestamp() * 1000),
        int(end_dt.timestamp() * 1000),
    )
    cutoff = next(
        (index for index, row in enumerate(rows) if row[0] >= start_dt.timestamp() * 1000),
        len(rows),
    )
    return rows, cutoff, start, end


def _evaluate(strategy_cls, rows, params, req, warmup=0):
    result = run_backtest(
        strategy_cls,
        rows,
        strategy_config=params,
        config=BacktestConfig(
            initial_capital=req.position_size_usdt or 100_000,
            allocation_pct=getattr(strategy_cls, "allocation_pct", 1),
        ),
        warmup_bars=warmup,
    )
    return result, _metrics(result)


def _run_python_optimize(
    req: OptimizeRequest, job_id: str | None = None
) -> GridSearchResultOut:
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy

    strategy_cls = get_strategy(req.strategy)
    rows, cutoff, start, end = _load_data(req)
    grid = _parameter_grid(strategy_cls)
    scored = []
    for index, params in enumerate(grid, 1):
        result, metrics = _evaluate(strategy_cls, rows, params, req, cutoff)
        scored.append((params, result, metrics))
        if job_id and job_id in _jobs:
            _jobs[job_id]["progress"] = {
                "completed": index,
                "total": len(grid),
            }
            check_cancelled(job_id)
    key = {
        "sharpe": lambda item: item[2]["sharpe"],
        "return": lambda item: item[2]["return"],
        "drawdown": lambda item: -item[2]["drawdown"],
    }.get(req.metric, lambda item: item[2]["sharpe"])
    scored.sort(key=key, reverse=True)
    output_rows = [
        GridRowOut(
            rank=index,
            params=params,
            sharpe=metrics["sharpe"],
            total_return_pct=metrics["return"] * 100,
            max_drawdown_pct=metrics["drawdown"] * 100,
            total_trades=int(metrics["trades"]),
            win_rate_pct=metrics["win_rate"] * 100,
        )
        for index, (params, _, metrics) in enumerate(scored[:20], 1)
    ]
    best = output_rows[0]
    return GridSearchResultOut(
        best_params=best.params,
        best_sharpe=best.sharpe,
        best_return_pct=best.total_return_pct,
        best_drawdown_pct=best.max_drawdown_pct,
        rows=output_rows,
        train_start=start,
        train_end=end,
    )


def _run_wfo(req: OptimizeRequest) -> WFOResultOut:
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy

    strategy_cls = get_strategy(req.strategy)
    rows, cutoff, _, _ = _load_data(req)
    period = rows[cutoff:]
    window_size = max(20, len(period) // 4)
    test_size = max(5, window_size // 3)
    windows = []
    for start in range(0, len(period) - window_size - test_size + 1, test_size):
        train = period[start : start + window_size]
        test = period[start + window_size : start + window_size + test_size]
        candidates = []
        for params in _parameter_grid(strategy_cls, limit=100):
            _, metric = _evaluate(strategy_cls, train, params, req)
            candidates.append((metric["sharpe"], params, metric))
        candidates.sort(reverse=True, key=lambda item: item[0])
        _, best, train_metric = candidates[0]
        _, test_metric = _evaluate(strategy_cls, test, best, req)
        def to_date(row):
            return datetime.fromtimestamp(
                row[0] / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        windows.append(
            WFOWindowOut(
                window=len(windows),
                train_start=to_date(train[0]),
                train_end=to_date(train[-1]),
                test_start=to_date(test[0]),
                test_end=to_date(test[-1]),
                best_params=best,
                train_sharpe=train_metric["sharpe"],
                train_return_pct=train_metric["return"] * 100,
                test_sharpe=test_metric["sharpe"],
                test_return_pct=test_metric["return"] * 100,
                test_drawdown_pct=test_metric["drawdown"] * 100,
            )
        )
    if not windows:
        raise ValueError("Not enough data for walk-forward optimization")
    positive = sum(window.test_return_pct > 0 for window in windows)
    return WFOResultOut(
        windows=windows,
        windows_count=len(windows),
        avg_train_return=sum(w.train_return_pct for w in windows) / len(windows),
        avg_test_return=sum(w.test_return_pct for w in windows) / len(windows),
        robustness_ratio=positive / len(windows),
        positive_windows=positive,
        total_test_return=sum(w.test_return_pct for w in windows),
    )


def _run_three_stage(req: OptimizeRequest) -> ThreeStageResultOut:
    grid = _run_python_optimize(req)
    wfo = _run_wfo(req)
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy

    strategy_cls = get_strategy(req.strategy)
    rows, cutoff, _, _ = _load_data(req)
    period = rows[cutoff:]
    holdout_index = max(1, int(len(period) * 0.8))
    holdout = period[holdout_index:]
    holdout_result, holdout_metrics = _evaluate(
        strategy_cls, holdout, grid.best_params, req
    )
    bh = (holdout[-1][4] / holdout[0][4] - 1) * 100 if holdout else 0
    degradation = (
        (grid.best_return_pct - holdout_metrics["return"] * 100)
        / abs(grid.best_return_pct)
        if grid.best_return_pct
        else 0
    )
    s1_pass = grid.best_sharpe >= 1 and grid.rows[0].total_trades >= 10
    s2_pass = wfo.robustness_ratio >= 0.5
    s3_pass = degradation <= 0.5 and holdout_metrics["sharpe"] >= 0.5
    return ThreeStageResultOut(
        best_params=grid.best_params,
        s1_in_sample_return=grid.best_return_pct,
        s1_in_sample_sharpe=grid.best_sharpe,
        s1_in_sample_drawdown=grid.best_drawdown_pct,
        s1_in_sample_trades=grid.rows[0].total_trades,
        s1_pass=s1_pass,
        s2_windows_count=wfo.windows_count,
        s2_avg_train_return=wfo.avg_train_return,
        s2_avg_test_return=wfo.avg_test_return,
        s2_robustness_ratio=wfo.robustness_ratio,
        s2_positive_windows=wfo.positive_windows,
        s2_total_test_return=wfo.total_test_return,
        s2_pass=s2_pass,
        s3_holdout_return=holdout_metrics["return"] * 100,
        s3_bh_return=bh,
        s3_holdout_sharpe=holdout_metrics["sharpe"],
        s3_sharpe_ci_lo=None,
        s3_sharpe_ci_hi=None,
        s3_holdout_drawdown=holdout_metrics["drawdown"] * 100,
        s3_holdout_trades=len(holdout_result.trades),
        s3_holdout_win_rate=holdout_metrics["win_rate"] * 100,
        s3_degradation=degradation,
        s3_pass=s3_pass,
        all_pass=s1_pass and s2_pass and s3_pass,
        bh_full_return=(period[-1][4] / period[0][4] - 1) * 100,
    )


async def run_optimize_job(job_id: str, req: OptimizeRequest) -> None:
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["mode"] = req.mode
    try:
        if req.mode == "grid":
            _jobs[job_id]["grid_result"] = await asyncio.to_thread(
                _run_python_optimize, req, job_id
            )
        elif req.mode == "wfo":
            _jobs[job_id]["wfo_result"] = await asyncio.to_thread(_run_wfo, req)
        else:
            _jobs[job_id]["full_result"] = await asyncio.to_thread(
                _run_three_stage, req
            )
        check_cancelled(job_id)
        _jobs[job_id]["status"] = "completed"
    except JobCancelled:
        _jobs[job_id]["status"] = "cancelled"
    except Exception as exc:
        import traceback

        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = (
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
