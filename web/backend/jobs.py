"""Background job management for backtest tasks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from web.backend.models import BacktestRequest, BacktestResultOut, TradeOut

# In-memory job store (process-scoped; resets on server restart)
_jobs: Dict[str, Dict[str, Any]] = {}


def create_job() -> str:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "result": None, "error": None}
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
