"""Background job management for backtest tasks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from web.backend.models import (
    BacktestRequest,
    BacktestResultOut,
    TradeOut,
    OptimizeRequest,
    GridRowOut,
    GridSearchResultOut,
    WFOWindowOut,
    WFOResultOut,
    ThreeStageResultOut,
    HeatmapMesaOut,
    HeatmapResultOut,
)

# In-memory job store (process-scoped; resets on server restart)
_jobs: Dict[str, Dict[str, Any]] = {}

_JOB_TTL = timedelta(hours=1)


def _cleanup_old_jobs() -> None:
    """Remove completed/failed jobs older than TTL."""
    now = datetime.now(timezone.utc)
    expired = [
        jid
        for jid, j in _jobs.items()
        if j["status"] in ("completed", "failed")
        and (now - j.get("created_at", now)) > _JOB_TTL
    ]
    for jid in expired:
        del _jobs[jid]


def create_job() -> str:
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)


def _downsample(series: pd.Series, max_points: int = 2000) -> pd.Series:
    """Thin a series to at most max_points by taking every nth row."""
    if len(series) <= max_points:
        return series
    step = len(series) // max_points
    return series.iloc[::step]


def _safe(v: Any) -> Any:
    """Convert numpy scalar to Python native type."""
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return v


def _serialize_result(
    result: Any,
    runner: Any,
    data: pd.DataFrame,
    metrics_full: Dict[str, float],
    bh_return: float,
    sharpe_lo: Optional[float],
    sharpe_hi: Optional[float],
    strategy_name: str,
    exchange: str,
    config_name: str,
) -> BacktestResultOut:
    """Convert BacktestResult + extended metrics to API response model."""

    equity = result.equity_curve
    initial = result.config.initial_capital

    # Guard: empty equity curve
    if len(equity) == 0:
        return BacktestResultOut(
            total_return_pct=0, bh_return_pct=0, annualized_return_pct=0,
            max_drawdown_pct=0, sharpe_ratio=0, sharpe_ci_lo=None, sharpe_ci_hi=None,
            sortino_ratio=0, calmar_ratio=0, total_trades=0, win_rate_pct=0,
            profit_factor=0, avg_win=0, avg_loss=0, expectancy=0,
            largest_win=0, largest_loss=0, final_equity=initial,
            equity_curve=[], drawdown_curve=[], monthly_returns=[], trades=[],
            strategy=strategy_name, exchange=exchange,
            period_start="", period_end="", config_name=config_name,
        )

    # Build B&H equity curve
    bh_raw = data["close"] / data["close"].iloc[0] * initial * runner.leverage
    bh_aligned = bh_raw.reindex(equity.index, method="ffill").fillna(initial)

    # Downsample both curves together
    equity_ds = _downsample(equity)
    bh_ds = bh_aligned.reindex(equity_ds.index, method="ffill").fillna(initial)

    equity_curve = [
        {"t": t.isoformat(), "strategy": _safe(v), "bh": _safe(b)}
        for (t, v), b in zip(equity_ds.items(), bh_ds.values)
    ]

    # Drawdown curve
    rolling_max = equity.cummax()
    dd = (equity - rolling_max) / rolling_max * 100
    dd_ds = _downsample(dd)
    drawdown_curve = [
        {"t": t.isoformat(), "dd": _safe(v)} for t, v in dd_ds.items()
    ]

    # Monthly returns
    monthly_eq = equity.resample("ME").last()
    monthly_ret = monthly_eq.pct_change().dropna() * 100
    monthly_returns = [
        {"year": t.year, "month": t.month, "return": _safe(v)}
        for t, v in monthly_ret.items()
    ]

    # Trades
    closing_trades = [t for t in result.trades if t.pnl != 0]
    trades_out = [
        TradeOut(
            timestamp=t.timestamp.isoformat(),
            side=t.side,
            price=_safe(t.price),
            amount=_safe(t.amount),
            fee=_safe(t.fee),
            pnl=_safe(t.pnl),
            pnl_pct=_safe(t.pnl_pct),
        )
        for t in closing_trades
    ]

    return BacktestResultOut(
        total_return_pct=_safe(metrics_full.get("total_return_pct", 0)),
        bh_return_pct=_safe(bh_return),
        annualized_return_pct=_safe(metrics_full.get("annualized_return_pct", 0)),
        max_drawdown_pct=_safe(metrics_full.get("max_drawdown_pct", 0)),
        sharpe_ratio=_safe(metrics_full.get("sharpe_ratio", 0)),
        sharpe_ci_lo=_safe(sharpe_lo) if sharpe_lo is not None else None,
        sharpe_ci_hi=_safe(sharpe_hi) if sharpe_hi is not None else None,
        sortino_ratio=_safe(metrics_full.get("sortino_ratio", 0)),
        calmar_ratio=_safe(metrics_full.get("calmar_ratio", 0)),
        total_trades=int(metrics_full.get("total_trades", 0)),
        win_rate_pct=_safe(metrics_full.get("win_rate_pct", 0)),
        profit_factor=_safe(metrics_full.get("profit_factor", 0)),
        avg_win=_safe(metrics_full.get("avg_win", 0)),
        avg_loss=_safe(metrics_full.get("avg_loss", 0)),
        expectancy=_safe(metrics_full.get("expectancy", 0)),
        largest_win=_safe(metrics_full.get("largest_win", 0)),
        largest_loss=_safe(metrics_full.get("largest_loss", 0)),
        final_equity=_safe(metrics_full.get("final_equity", initial)),
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=monthly_returns,
        trades=trades_out,
        strategy=strategy_name,
        exchange=exchange,
        period_start=equity.index[0].isoformat(),
        period_end=equity.index[-1].isoformat(),
        config_name=config_name,
    )


async def run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """Run a backtest in the background and store the result."""
    _jobs[job_id]["status"] = "running"

    try:
        from datetime import datetime, timedelta, timezone
        from strategy.backtest.exchange_profiles import get_profile
        from strategy.backtest.runner import BacktestRunner, _bh_return_pct, _bootstrap_sharpe_ci
        from strategy.backtest.utils import PERIODS, fetch_data, fetch_funding_rates
        from nexustrader.backtest.analysis.performance import (
            PerformanceAnalyzer,
            infer_periods_per_year,
        )

        profile = get_profile(req.exchange)
        symbol = req.symbol or profile.default_symbol

        # Determine date range
        if req.start_date:
            start_date = datetime.strptime(req.start_date, "%Y-%m-%d")
            end_date = (
                datetime.strptime(req.end_date, "%Y-%m-%d")
                if req.end_date
                else datetime.now(timezone.utc).replace(tzinfo=None)
            )
        else:
            days = PERIODS.get(req.period or "1y", 365)
            end_date = datetime.now(timezone.utc).replace(tzinfo=None)
            start_date = end_date - timedelta(days=days)

        # Fetch data (async)
        data = await fetch_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            exchange=profile.ccxt_id,
        )
        funding_rates = await fetch_funding_rates(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            exchange=profile.ccxt_id,
        )

        runner = BacktestRunner(
            strategy_name=req.strategy,
            exchange=req.exchange,
            symbol=symbol,
            leverage=req.leverage,
        )

        # Run backtest (CPU-bound → thread pool)
        result_dict = await asyncio.to_thread(
            runner.run_single,
            data=data,
            mesa_index=req.mesa_index,
            period=req.period,
            funding_rates=funding_rates,
        )

        bt_result = result_dict.get("result")
        if bt_result is None:
            raise ValueError("Backtest returned no result")

        # Full metrics from PerformanceAnalyzer
        bt_config = runner._create_bt_config(data)
        analyzer = PerformanceAnalyzer(
            equity_curve=bt_result.equity_curve,
            trades=bt_result.trades,
            initial_capital=bt_config.initial_capital,
        )
        metrics_full = analyzer.calculate_metrics()

        bh_return = _bh_return_pct(data, req.leverage)
        ppy = infer_periods_per_year(bt_result.equity_curve.index)
        sharpe_lo, sharpe_hi = _bootstrap_sharpe_ci(bt_result.equity_curve, ppy)

        out = _serialize_result(
            result=bt_result,
            runner=runner,
            data=data,
            metrics_full=metrics_full,
            bh_return=bh_return,
            sharpe_lo=sharpe_lo,
            sharpe_hi=sharpe_hi,
            strategy_name=req.strategy,
            exchange=req.exchange,
            config_name=result_dict.get("config_name", "Custom"),
        )

        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["result"] = out

    except Exception as exc:
        import traceback
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


# ─── Optimize job ─────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return float(v) if v is not None else 0.0


def _serialize_grid(opt_result: dict) -> GridSearchResultOut:
    results = opt_result["results"]
    train_data = opt_result["train_data"]
    rows = []
    for i, r in enumerate(results[:20]):
        m = r.metrics
        rows.append(GridRowOut(
            rank=i + 1,
            params=r.params,
            sharpe=_safe_float(m.get("sharpe_ratio", 0)),
            total_return_pct=_safe_float(m.get("total_return_pct", 0)),
            max_drawdown_pct=_safe_float(m.get("max_drawdown_pct", 0)),
            total_trades=int(m.get("total_trades", 0)),
            win_rate_pct=_safe_float(m.get("win_rate_pct", 0)),
        ))
    best = opt_result["best_metrics"]
    return GridSearchResultOut(
        best_params=opt_result["best_params"],
        best_sharpe=_safe_float(best.get("sharpe_ratio", 0)),
        best_return_pct=_safe_float(best.get("total_return_pct", 0)),
        best_drawdown_pct=_safe_float(best.get("max_drawdown_pct", 0)),
        rows=rows,
        train_start=str(train_data.index[0].date()),
        train_end=str(train_data.index[-1].date()),
    )


def _serialize_wfo(wfo_result: dict) -> WFOResultOut:
    windows = []
    for w in wfo_result["results"]:
        windows.append(WFOWindowOut(
            window=w.window_index,
            train_start=w.train_start.strftime("%Y-%m-%d"),
            train_end=w.train_end.strftime("%Y-%m-%d"),
            test_start=w.test_start.strftime("%Y-%m-%d"),
            test_end=w.test_end.strftime("%Y-%m-%d"),
            best_params=w.best_params,
            train_sharpe=_safe_float(w.train_metrics.get("sharpe_ratio", 0)),
            train_return_pct=_safe_float(w.train_metrics.get("total_return_pct", 0)),
            test_sharpe=_safe_float(w.test_metrics.get("sharpe_ratio", 0)),
            test_return_pct=_safe_float(w.test_metrics.get("total_return_pct", 0)),
            test_drawdown_pct=_safe_float(w.test_metrics.get("max_drawdown_pct", 0)),
        ))
    s = wfo_result["summary"]
    return WFOResultOut(
        windows=windows,
        windows_count=int(s.get("windows_count", 0)),
        avg_train_return=_safe_float(s.get("avg_train_return", 0)),
        avg_test_return=_safe_float(s.get("avg_test_return", 0)),
        robustness_ratio=_safe_float(s.get("robustness_ratio", 0)),
        positive_windows=int(s.get("positive_test_windows", 0)),
        total_test_return=_safe_float(s.get("total_test_return", 0)),
    )


def _serialize_three_stage(results: dict) -> ThreeStageResultOut:
    s1 = results["stage1"]
    s2 = results["stage2"]
    s3 = results["stage3"]
    sm = results["summary"]
    wc = max(s2["windows_count"], 1)
    pos_pct = s2["positive_windows"] / wc
    robustness_pass = s2["robustness_ratio"] >= 0.5
    consistency_pass = pos_pct >= 0.5
    s2_pass = robustness_pass and consistency_pass

    is1 = _safe_float(s1["in_sample_return"])
    hs3 = _safe_float(s3["holdout_return"])
    if is1 > 0:
        deg = 1 - (hs3 / is1) if hs3 >= 0 else 1.0
    else:
        deg = 0.0 if hs3 <= is1 else 1.0

    return ThreeStageResultOut(
        best_params=s1["best_params"],
        s1_in_sample_return=_safe_float(s1["in_sample_return"]),
        s1_in_sample_sharpe=_safe_float(s1["in_sample_sharpe"]),
        s1_in_sample_drawdown=_safe_float(s1["in_sample_drawdown"]),
        s1_in_sample_trades=int(s1["in_sample_trades"]),
        s1_pass=bool(sm["stage1_pass"]),
        s2_windows_count=int(s2["windows_count"]),
        s2_avg_train_return=_safe_float(s2["avg_train_return"]),
        s2_avg_test_return=_safe_float(s2["avg_test_return"]),
        s2_robustness_ratio=_safe_float(s2["robustness_ratio"]),
        s2_positive_windows=int(s2["positive_windows"]),
        s2_total_test_return=_safe_float(s2["total_test_return"]),
        s2_pass=s2_pass,
        s3_holdout_return=_safe_float(s3["holdout_return"]),
        s3_bh_return=_safe_float(s3["bh_holdout_return"]),
        s3_holdout_sharpe=_safe_float(s3["holdout_sharpe"]),
        s3_sharpe_ci_lo=s3.get("holdout_sharpe_ci_lo"),
        s3_sharpe_ci_hi=s3.get("holdout_sharpe_ci_hi"),
        s3_holdout_drawdown=_safe_float(s3["holdout_drawdown"]),
        s3_holdout_trades=int(s3["holdout_trades"]),
        s3_holdout_win_rate=_safe_float(s3["holdout_win_rate"]),
        s3_degradation=_safe_float(deg),
        s3_pass=bool(sm["stage3_pass"]),
        all_pass=bool(sm["all_pass"]),
        bh_full_return=_safe_float(sm["bh_full_return"]),
    )


def _serialize_heatmap(hm_results, hc) -> HeatmapResultOut:
    """Serialize HeatmapResults to HeatmapResultOut."""
    import math

    panel = hm_results.panels[0] if hm_results.panels else {}
    raw_sharpe = panel.get("sharpe_grid", [])

    def _clean_grid(grid):
        out = []
        for row in grid:
            cleaned = []
            for v in row:
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    cleaned.append(None)
                else:
                    cleaned.append(round(float(v), 4))
            out.append(cleaned)
        return out

    sharpe_grid = _clean_grid(raw_sharpe) if raw_sharpe else []
    # Build return_grid from panel metrics if available
    return_grid_raw = panel.get("return_grid", [])
    return_grid = _clean_grid(return_grid_raw) if return_grid_raw else []

    mesas = []
    for m in hm_results.mesas[:10]:
        mesas.append(HeatmapMesaOut(
            index=m.index,
            center_x=float(m.center_x),
            center_y=float(m.center_y),
            avg_sharpe=round(float(m.avg_sharpe), 3),
            avg_return_pct=round(float(m.avg_return_pct), 2),
            stability=round(float(m.stability), 3),
            area=int(m.area),
            frequency_label=m.frequency_label,
        ))

    return HeatmapResultOut(
        x_values=[float(v) for v in hm_results.x_values],
        y_values=[float(v) for v in hm_results.y_values],
        x_label=hc.x_label,
        y_label=hc.y_label,
        x_param=hc.x_param_name,
        y_param=hc.y_param_name,
        sharpe_grid=sharpe_grid,
        return_grid=return_grid,
        mesas=mesas,
    )


async def run_optimize_job(job_id: str, req: OptimizeRequest) -> None:
    """Run optimization in the background and store the result."""
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["mode"] = req.mode

    try:
        from datetime import datetime, timedelta, timezone
        from strategy.backtest.exchange_profiles import get_profile
        from strategy.backtest.runner import BacktestRunner
        from strategy.backtest.utils import PERIODS, fetch_data, fetch_funding_rates

        profile = get_profile(req.exchange)
        symbol = req.symbol or profile.default_symbol

        if req.start_date:
            start_date = datetime.strptime(req.start_date, "%Y-%m-%d")
            end_date = (
                datetime.strptime(req.end_date, "%Y-%m-%d")
                if req.end_date
                else datetime.now(timezone.utc).replace(tzinfo=None)
            )
        else:
            days = PERIODS.get(req.period or "1y", 365)
            end_date = datetime.now(timezone.utc).replace(tzinfo=None)
            start_date = end_date - timedelta(days=days)

        data = await fetch_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            exchange=profile.ccxt_id,
        )
        funding_rates = await fetch_funding_rates(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            exchange=profile.ccxt_id,
        )

        runner = BacktestRunner(
            strategy_name=req.strategy,
            exchange=req.exchange,
            symbol=symbol,
            leverage=req.leverage,
            n_jobs=req.n_jobs,
        )

        mode = req.mode

        if mode == "grid":
            opt = await asyncio.to_thread(
                runner.run_grid_search,
                data=data,
                period=req.period,
                funding_rates=funding_rates,
            )
            _jobs[job_id]["grid_result"] = _serialize_grid(opt)

        elif mode == "wfo":
            wfo = await asyncio.to_thread(
                runner.run_walk_forward,
                data=data,
                funding_rates=funding_rates,
            )
            _jobs[job_id]["wfo_result"] = _serialize_wfo(wfo)

        elif mode == "full":
            results = await asyncio.to_thread(
                runner.run_three_stage_test,
                data=data,
                funding_rates=funding_rates,
                period=req.period or "1y",
            )
            _jobs[job_id]["full_result"] = _serialize_three_stage(results)

        elif mode == "heatmap":
            from strategy.backtest.heatmap import HeatmapScanner
            from strategy.backtest.registry import get_strategy
            import numpy as np

            reg = get_strategy(req.strategy)
            hc = reg.heatmap_config
            has_funding = funding_rates is not None and not funding_rates.empty
            cost_config = profile.cost_config(use_funding_rate=has_funding)

            x_vals = np.linspace(hc.x_range[0], hc.x_range[1], req.resolution)
            y_vals = np.linspace(hc.y_range[0], hc.y_range[1], req.resolution)

            scanner = HeatmapScanner(
                data=data,
                signal_generator_cls=reg.signal_generator_cls,
                config_cls=reg.config_cls,
                filter_config_cls=reg.filter_config_cls,
                funding_rates=funding_rates if has_funding else None,
                x_param_name=hc.x_param_name,
                y_param_name=hc.y_param_name,
                filter_config_factory=hc.filter_config_factory,
                symbol=symbol,
                cost_config=cost_config,
                interval=reg.default_interval,
                leverage=req.leverage,
                n_jobs=req.n_jobs,
            )
            fixed_params = dict(hc.fixed_params)
            hm_results = await asyncio.to_thread(
                scanner.scan,
                x_values=x_vals,
                y_values=y_vals,
                fixed_params=fixed_params,
            )
            _jobs[job_id]["heatmap_result"] = _serialize_heatmap(hm_results, hc)

        else:
            raise ValueError(f"Unknown optimize mode: {mode!r}")

        _jobs[job_id]["status"] = "completed"

    except Exception as exc:
        import traceback
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
