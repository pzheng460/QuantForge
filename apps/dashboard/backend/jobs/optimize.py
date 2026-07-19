"""Optimize job runners: grid search, walk-forward, and three-stage validation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np

from apps.dashboard.backend.jobs.data import (
    _DEFAULT_SYMBOLS,
    _fetch_ohlcv,
    _ohlcv_to_bars,
    _resolve_date_range,
    _resolve_pine_source,
    _TF_MS,
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
from quantforge.pine.live.connector import timeframe_to_seconds


def _safe_float(v) -> float:
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return float(v) if v is not None else 0.0


def _run_wfo(req: OptimizeRequest) -> WFOResultOut:
    """Execute Walk-Forward Optimization."""
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        generate_grid,
        run_optimization,
    )
    from quantforge.pine.parser.parser import parse

    source = _resolve_pine_source(req.strategy, req.pine_source)
    ast = parse(source)

    inputs = extract_pine_inputs(ast)
    if not inputs:
        raise ValueError(
            "No input.int() / input.float() parameters found in Pine Script"
        )

    grid = generate_grid(inputs)

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_seconds = timeframe_to_seconds(req.timeframe) * req.warmup_bars
    warmup_start = start_dt - timedelta(seconds=warmup_seconds)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)

    # Find warmup cutoff
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_bar_count = 0
    for bar in all_ohlcv:
        if bar[0] >= start_ms:
            break
        warmup_bar_count += 1

    # Extract period data (after warmup)
    period_bars = bars[warmup_bar_count:]
    total_bars = len(period_bars)

    # WFO parameters: 60-day windows with 30-day steps
    window_days = 60
    step_days = 30
    bar_ms = _TF_MS.get(req.timeframe, 3_600_000)
    bars_per_day = 86_400_000 // bar_ms
    window_bars = window_days * bars_per_day
    step_bars = step_days * bars_per_day

    windows = []
    offset = 0
    while offset + window_bars + step_bars <= total_bars:
        train_start_idx = offset
        train_end_idx = offset + window_bars
        test_start_idx = train_end_idx
        test_end_idx = min(test_start_idx + step_bars, total_bars)

        if (
            test_end_idx - test_start_idx < step_bars // 2
        ):  # Skip if test period too small
            break

        windows.append(
            {
                "train_start_idx": train_start_idx,
                "train_end_idx": train_end_idx,
                "test_start_idx": test_start_idx,
                "test_end_idx": test_end_idx,
            }
        )
        offset += step_bars

    if not windows:
        raise ValueError(
            f"Not enough data for WFO. Need at least {window_days + step_days} days"
        )

    wfo_results = []
    for i, w in enumerate(windows):
        # Train period
        train_bars = period_bars[w["train_start_idx"] : w["train_end_idx"]]
        train_optimization = run_optimization(
            ast=ast,
            bars=train_bars,
            grid=grid,
            metric=req.metric,
            position_size_usdt=req.position_size_usdt,
            leverage=req.leverage,
        )
        best_params = train_optimization[0].params if train_optimization else {}
        train_sharpe = (
            _safe_float(train_optimization[0].sharpe) if train_optimization else 0.0
        )
        train_return = (
            _safe_float(train_optimization[0].return_pct * 100)
            if train_optimization
            else 0.0
        )

        # Test period with best params
        test_bars = period_bars[w["test_start_idx"] : w["test_end_idx"]]
        test_optimization = run_optimization(
            ast=ast,
            bars=test_bars,
            grid=[best_params],
            metric=req.metric,
            position_size_usdt=req.position_size_usdt,
            leverage=req.leverage,
        )
        test_sharpe = (
            _safe_float(test_optimization[0].sharpe) if test_optimization else 0.0
        )
        test_return = (
            _safe_float(test_optimization[0].return_pct * 100)
            if test_optimization
            else 0.0
        )
        test_drawdown = (
            _safe_float(test_optimization[0].max_drawdown * 100)
            if test_optimization
            else 0.0
        )

        # Date strings
        train_start_bar = all_ohlcv[warmup_bar_count + w["train_start_idx"]]
        train_end_bar = all_ohlcv[warmup_bar_count + w["train_end_idx"] - 1]
        test_start_bar = all_ohlcv[warmup_bar_count + w["test_start_idx"]]
        test_end_bar = all_ohlcv[warmup_bar_count + w["test_end_idx"] - 1]

        wfo_results.append(
            WFOWindowOut(
                window=i,
                train_start=datetime.fromtimestamp(
                    train_start_bar[0] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                train_end=datetime.fromtimestamp(
                    train_end_bar[0] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                test_start=datetime.fromtimestamp(
                    test_start_bar[0] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                test_end=datetime.fromtimestamp(
                    test_end_bar[0] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d"),
                best_params=best_params,
                train_sharpe=train_sharpe,
                train_return_pct=train_return,
                test_sharpe=test_sharpe,
                test_return_pct=test_return,
                test_drawdown_pct=test_drawdown,
            )
        )

    # Aggregate metrics
    test_returns = [w.test_return_pct for w in wfo_results]
    positive_windows = sum(1 for r in test_returns if r > 0)
    avg_train_return = (
        sum(w.train_return_pct for w in wfo_results) / len(wfo_results)
        if wfo_results
        else 0.0
    )
    avg_test_return = sum(test_returns) / len(test_returns) if test_returns else 0.0
    total_test_return = sum(test_returns)
    robustness_ratio = positive_windows / len(wfo_results) if wfo_results else 0.0

    return WFOResultOut(
        windows=wfo_results,
        windows_count=len(wfo_results),
        avg_train_return=avg_train_return,
        avg_test_return=avg_test_return,
        robustness_ratio=robustness_ratio,
        positive_windows=positive_windows,
        total_test_return=total_test_return,
    )


def _run_three_stage(req: OptimizeRequest) -> ThreeStageResultOut:
    """Execute Three-Stage Pipeline (In-Sample → WFO → Holdout)."""
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        generate_grid,
        run_optimization,
    )
    from quantforge.pine.parser.parser import parse

    source = _resolve_pine_source(req.strategy, req.pine_source)
    ast = parse(source)

    inputs = extract_pine_inputs(ast)
    if not inputs:
        raise ValueError(
            "No input.int() / input.float() parameters found in Pine Script"
        )

    grid = generate_grid(inputs)

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_seconds = timeframe_to_seconds(req.timeframe) * req.warmup_bars
    warmup_start = start_dt - timedelta(seconds=warmup_seconds)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)

    # Find warmup cutoff
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_bar_count = 0
    for bar in all_ohlcv:
        if bar[0] >= start_ms:
            break
        warmup_bar_count += 1

    # Extract period data and calculate splits
    period_bars = bars[warmup_bar_count:]
    total_bars = len(period_bars)

    s1_end = int(total_bars * 0.6)  # 60% for Stage 1
    s2_end = int(total_bars * 0.8)  # 60-80% for Stage 2 WFO
    # Stage 3 uses remaining 80-100%

    # Stage 1: In-sample optimization
    s1_bars = period_bars[:s1_end]
    s1_optimization = run_optimization(
        ast=ast,
        bars=s1_bars,
        grid=grid,
        metric=req.metric,
        position_size_usdt=req.position_size_usdt,
        leverage=req.leverage,
    )
    best_params = s1_optimization[0].params if s1_optimization else {}
    s1_return = (
        _safe_float(s1_optimization[0].return_pct * 100) if s1_optimization else 0.0
    )
    s1_sharpe = _safe_float(s1_optimization[0].sharpe) if s1_optimization else 0.0
    s1_drawdown = (
        _safe_float(s1_optimization[0].max_drawdown * 100) if s1_optimization else 0.0
    )
    s1_trades = int(s1_optimization[0].total_trades) if s1_optimization else 0
    s1_pass = s1_sharpe >= 1.0 and s1_trades >= 10

    # Stage 2: Walk-forward validation on 60-80% data
    s2_bars = period_bars[s1_end:s2_end]
    s2_bar_count = len(s2_bars)

    # WFO with smaller windows for Stage 2
    window_days = 30
    step_days = 14
    bar_ms = _TF_MS.get(req.timeframe, 3_600_000)
    bars_per_day = 86_400_000 // bar_ms
    window_bars = window_days * bars_per_day
    step_bars = step_days * bars_per_day

    s2_windows = []
    offset = 0
    while offset + window_bars + step_bars <= s2_bar_count:
        train_end = offset + window_bars
        test_start = train_end
        test_end = min(test_start + step_bars, s2_bar_count)

        if test_end - test_start < step_bars // 2:
            break

        # Test with best params from Stage 1
        test_bars = s2_bars[test_start:test_end]

        test_results = run_optimization(
            ast=ast,
            bars=test_bars,
            grid=[best_params],
            metric=req.metric,
            position_size_usdt=req.position_size_usdt,
            leverage=req.leverage,
        )
        test_return = (
            _safe_float(test_results[0].return_pct * 100) if test_results else 0.0
        )

        s2_windows.append(test_return)
        offset += step_bars

    s2_positive_windows = sum(1 for r in s2_windows if r > 0)
    s2_avg_train_return = s1_return  # Using S1 best params
    s2_avg_test_return = sum(s2_windows) / len(s2_windows) if s2_windows else 0.0
    s2_total_test_return = sum(s2_windows)
    s2_robustness_ratio = s2_positive_windows / len(s2_windows) if s2_windows else 0.0
    s2_pass = s2_robustness_ratio >= 0.5 and s2_positive_windows >= len(s2_windows) // 2

    # Stage 3: Final holdout test (80-100% data)
    s3_bars = period_bars[s2_end:]
    s3_optimization = run_optimization(
        ast=ast,
        bars=s3_bars,
        grid=[best_params],
        metric=req.metric,
        position_size_usdt=req.position_size_usdt,
        leverage=req.leverage,
    )
    s3_return = (
        _safe_float(s3_optimization[0].return_pct * 100) if s3_optimization else 0.0
    )
    s3_sharpe = _safe_float(s3_optimization[0].sharpe) if s3_optimization else 0.0
    s3_drawdown = (
        _safe_float(s3_optimization[0].max_drawdown * 100) if s3_optimization else 0.0
    )
    s3_trades = int(s3_optimization[0].total_trades) if s3_optimization else 0
    s3_win_rate = (
        _safe_float(s3_optimization[0].win_rate * 100) if s3_optimization else 0.0
    )

    # Bootstrap confidence interval for Sharpe (simplified)
    n_bootstrap = 100
    s3_equity = s3_optimization[0].equity_curve if s3_optimization else []
    sharpe_samples = []
    if len(s3_equity) > 10:
        for _ in range(n_bootstrap):
            indices = np.random.choice(
                len(s3_equity), size=len(s3_equity), replace=True
            )
            sample_equity = [s3_equity[i] for i in indices]
            # Simple Sharpe calculation
            returns = []
            for i in range(1, len(sample_equity)):
                if sample_equity[i - 1] > 0:
                    returns.append(
                        (sample_equity[i] - sample_equity[i - 1]) / sample_equity[i - 1]
                    )
            if returns:
                mean_r = sum(returns) / len(returns)
                var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
                std_r = (var_r) ** 0.5 if var_r > 0 else 0.0
                if std_r > 0:
                    sharpe_samples.append((mean_r / std_r) * (252**0.5))  # Annualized

    sharpe_samples.sort()
    s3_sharpe_ci_lo = (
        sharpe_samples[5] if len(sharpe_samples) >= 10 else None
    )  # 5th percentile
    s3_sharpe_ci_hi = (
        sharpe_samples[94] if len(sharpe_samples) >= 10 else None
    )  # 95th percentile

    # Buy & Hold calculation for S3 period
    s3_ohlcv_start = all_ohlcv[warmup_bar_count + s2_end]
    s3_ohlcv_end = all_ohlcv[warmup_bar_count + len(period_bars) - 1]
    s3_bh_return = (s3_ohlcv_end[4] / s3_ohlcv_start[4] - 1) * 100

    # Full period B&H
    full_start_price = all_ohlcv[warmup_bar_count][4]
    full_end_price = all_ohlcv[-1][4]
    bh_full_return = (full_end_price / full_start_price - 1) * 100

    # Degradation: how much worse is S3 vs S1
    s3_degradation = (s1_return - s3_return) / abs(s1_return) if s1_return != 0 else 0.0
    s3_pass = s3_degradation <= 0.5 and s3_sharpe >= 0.5

    all_pass = s1_pass and s2_pass and s3_pass

    return ThreeStageResultOut(
        best_params=best_params,
        s1_in_sample_return=s1_return,
        s1_in_sample_sharpe=s1_sharpe,
        s1_in_sample_drawdown=s1_drawdown,
        s1_in_sample_trades=s1_trades,
        s1_pass=s1_pass,
        s2_windows_count=len(s2_windows),
        s2_avg_train_return=s2_avg_train_return,
        s2_avg_test_return=s2_avg_test_return,
        s2_robustness_ratio=s2_robustness_ratio,
        s2_positive_windows=s2_positive_windows,
        s2_total_test_return=s2_total_test_return,
        s2_pass=s2_pass,
        s3_holdout_return=s3_return,
        s3_bh_return=s3_bh_return,
        s3_holdout_sharpe=s3_sharpe,
        s3_sharpe_ci_lo=s3_sharpe_ci_lo,
        s3_sharpe_ci_hi=s3_sharpe_ci_hi,
        s3_holdout_drawdown=s3_drawdown,
        s3_holdout_trades=s3_trades,
        s3_holdout_win_rate=s3_win_rate,
        s3_degradation=s3_degradation,
        s3_pass=s3_pass,
        all_pass=all_pass,
        bh_full_return=bh_full_return,
    )


def _run_pine_optimize(
    req: OptimizeRequest, job_id: str | None = None
) -> GridSearchResultOut:
    """Execute Pine Script grid optimization synchronously."""
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        generate_grid,
        run_optimization,
    )
    from quantforge.pine.parser.parser import parse

    source = _resolve_pine_source(req.strategy, req.pine_source)
    ast = parse(source)

    inputs = extract_pine_inputs(ast)
    if not inputs:
        raise ValueError(
            "No input.int() / input.float() parameters found in Pine Script"
        )

    grid = generate_grid(inputs)

    # Seed the progress so the frontend immediately knows the total combo count.
    if job_id and job_id in _jobs:
        _jobs[job_id]["progress"] = {
            "completed": 0,
            "total": len(grid),
            "avg_secs_per_combo": None,
            "elapsed_secs": 0.0,
        }

    # Capture wall-clock start so the progress callback can report ETA.
    import time as _time

    grid_start = _time.monotonic()

    def _on_progress(completed: int, total: int) -> None:
        if not (job_id and job_id in _jobs):
            return
        elapsed = _time.monotonic() - grid_start
        avg = elapsed / completed if completed > 0 else None
        _jobs[job_id]["progress"] = {
            "completed": completed,
            "total": total,
            "avg_secs_per_combo": avg,
            "elapsed_secs": elapsed,
        }

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_seconds = timeframe_to_seconds(req.timeframe) * req.warmup_bars
    warmup_start = start_dt - timedelta(seconds=warmup_seconds)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_bar_count = 0
    for bar in all_ohlcv:
        if bar[0] >= start_ms:
            break
        warmup_bar_count += 1

    results = run_optimization(
        ast=ast,
        bars=bars,
        grid=grid,
        warmup_count=warmup_bar_count,
        metric=req.metric,
        progress_cb=_on_progress,
        position_size_usdt=req.position_size_usdt,
        leverage=req.leverage,
    )

    rows = []
    for i, r in enumerate(results[:20]):
        rows.append(
            GridRowOut(
                rank=i + 1,
                params=r.params,
                sharpe=_safe_float(r.sharpe),
                total_return_pct=_safe_float(r.return_pct * 100),
                max_drawdown_pct=_safe_float(r.max_drawdown * 100),
                total_trades=int(r.total_trades),
                win_rate_pct=_safe_float(r.win_rate * 100),
            )
        )

    best = results[0] if results else None
    return GridSearchResultOut(
        best_params=best.params if best else {},
        best_sharpe=_safe_float(best.sharpe) if best else 0.0,
        best_return_pct=_safe_float(best.return_pct * 100) if best else 0.0,
        best_drawdown_pct=_safe_float(best.max_drawdown * 100) if best else 0.0,
        rows=rows,
        train_start=start_str,
        train_end=end_str,
    )


async def run_optimize_job(job_id: str, req: OptimizeRequest) -> None:
    """Run Pine Script optimization in the background and store the result."""
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["mode"] = req.mode

    try:
        if req.mode == "grid":
            result = await asyncio.to_thread(_run_pine_optimize, req, job_id)
            check_cancelled(job_id)
            _jobs[job_id]["grid_result"] = result
        elif req.mode == "wfo":
            result = await asyncio.to_thread(_run_wfo, req)
            check_cancelled(job_id)
            _jobs[job_id]["wfo_result"] = result
        elif req.mode == "full":
            result = await asyncio.to_thread(_run_three_stage, req)
            check_cancelled(job_id)
            _jobs[job_id]["full_result"] = result
        else:
            raise ValueError(f"Unknown optimization mode: {req.mode}")

        _jobs[job_id]["status"] = "completed"  # status AFTER result to avoid race

    except JobCancelled:
        _jobs[job_id]["status"] = "cancelled"

    except Exception as exc:
        import traceback

        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = (
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
