"""Background job management for backtest tasks."""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

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
_cancel_flags: Dict[str, threading.Event] = {}

_JOB_TTL = timedelta(hours=1)


class JobCancelled(Exception):
    """Raised when a job is cancelled via the cancel flag."""

    pass


def check_cancelled(job_id: str) -> None:
    """Check if a job has been cancelled; raise JobCancelled if so."""
    flag = _cancel_flags.get(job_id)
    if flag and flag.is_set():
        raise JobCancelled(f"Job {job_id} was cancelled")


def cancel_job(job_id: str) -> bool:
    """Cancel a running job. Returns True if successfully cancelled."""
    job = _jobs.get(job_id)
    if job is None:
        return False
    if job["status"] not in ("pending", "running"):
        return False
    flag = _cancel_flags.get(job_id)
    if flag:
        flag.set()
    job["status"] = "cancelled"
    return True


def _cleanup_old_jobs() -> None:
    """Remove completed/failed/cancelled jobs older than TTL."""
    now = datetime.now(timezone.utc)
    expired = [
        jid
        for jid, j in _jobs.items()
        if j["status"] in ("completed", "failed", "cancelled")
        and (now - j.get("created_at", now)) > _JOB_TTL
    ]
    for jid in expired:
        del _jobs[jid]
        _cancel_flags.pop(jid, None)


def create_job() -> str:
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }
    _cancel_flags[job_id] = threading.Event()
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)


async def run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """Run a Pine Script backtest in the background and store the result."""
    _jobs[job_id]["status"] = "running"

    try:
        result = await asyncio.to_thread(_run_pine_backtest, req)
        check_cancelled(job_id)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = "completed"

    except JobCancelled:
        _jobs[job_id]["status"] = "cancelled"

    except Exception as exc:
        import traceback

        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = (
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )


_PERIOD_DAYS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
}

_DEFAULT_SYMBOLS = {
    "bitget": "BTC/USDT:USDT",
    "binance": "BTC/USDT:USDT",
    "okx": "BTC/USDT:USDT",
    "bybit": "BTC/USDT:USDT",
    "hyperliquid": "BTC/USDT:USDT",
}

_STRATEGIES_DIR = (
    Path(__file__).resolve().parents[2] / "quantforge" / "pine" / "strategies"
)


def _resolve_pine_source(
    strategy: Optional[str],
    pine_source: Optional[str],
    config_override: Optional[dict] = None,
) -> str:
    """Return Pine Script source from either raw source or strategy file name."""
    if pine_source:
        return pine_source

    pine_file = _STRATEGIES_DIR / f"{strategy}.pine"
    if not pine_file.exists():
        raise FileNotFoundError(f"Strategy file not found: {pine_file}")

    source = pine_file.read_text()

    # Apply config_override: replace input default values
    if config_override:
        import re

        for var_name, value in config_override.items():
            pattern = rf"({var_name}\s*=\s*input\.(?:int|float)\()(\d+(?:\.\d+)?)"
            replacement = rf"\g<1>{value}"
            source = re.sub(pattern, replacement, source)

    return source


def _resolve_date_range(
    period: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[str, str]:
    """Return (start_str, end_str) from either explicit dates or period shorthand."""
    if start_date:
        return start_date, end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    days = _PERIOD_DAYS.get(period or "1y", 365)
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


_TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def _fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch OHLCV bars from an exchange via ccxt.

    Uses fixed-window pagination to avoid gaps with exchanges (like Bitget)
    that return limited results per request.
    """
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    bar_ms = _TF_MS.get(timeframe, 3_600_000)
    batch_size = 200  # conservative; most exchanges return 200-1000
    window_ms = bar_ms * batch_size  # time span per request

    seen: set[int] = set()
    all_ohlcv: list[list] = []
    current = since_ms

    while current < end_ms:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=current, limit=batch_size)
        if not ohlcv:
            # No data for this window — advance by window size
            current += window_ms
            continue

        for bar in ohlcv:
            ts = bar[0]
            if ts not in seen and ts <= end_ms:
                seen.add(ts)
                all_ohlcv.append(bar)

        last_ts = ohlcv[-1][0]
        if last_ts <= current:
            # Exchange returned stale data — advance by window size
            current += window_ms
        else:
            current = last_ts + 1

    all_ohlcv.sort(key=lambda b: b[0])
    if not all_ohlcv:
        raise ValueError("No OHLCV data returned from exchange")
    return all_ohlcv


def _ohlcv_to_bars(all_ohlcv: list[list]) -> list:
    """Convert raw OHLCV lists to BarData objects."""
    from quantforge.pine.interpreter.context import BarData

    return [
        BarData(
            open=bar[1],
            high=bar[2],
            low=bar[3],
            close=bar[4],
            volume=bar[5],
            time=bar[0] // 1000,
        )
        for bar in all_ohlcv
    ]


def _run_pine_backtest(req: BacktestRequest) -> BacktestResultOut:
    """Execute a Pine Script backtest synchronously (called from thread pool)."""
    from quantforge.pine.interpreter.context import ExecutionContext
    from quantforge.pine.interpreter.runtime import PineRuntime
    from quantforge.pine.parser.parser import parse

    source = _resolve_pine_source(req.strategy, req.pine_source, req.config_override)
    ast = parse(source)

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(days=req.warmup_days)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)

    # Find the bar index where actual backtest period starts (after warmup)
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_bar_count = 0
    for bar in all_ohlcv:
        if bar[0] >= start_ms:
            break
        warmup_bar_count += 1

    # Run backtest
    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)

    # Filter out trades from warmup period
    all_trades = result.trades
    trades = [t for t in all_trades if t.entry_bar >= warmup_bar_count]
    initial_capital = result.initial_capital

    # Recompute metrics on filtered trades
    total_pnl = sum(t.pnl for t in trades)
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = total - wins
    win_rate = (wins / total * 100) if total > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    avg_win = gross_profit / wins if wins > 0 else 0.0
    avg_loss = gross_loss / losses if losses > 0 else 0.0
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    largest_win = max((t.pnl for t in trades if t.pnl > 0), default=0.0)
    largest_loss = min((t.pnl for t in trades if t.pnl < 0), default=0.0)

    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    cur_wins = 0
    cur_losses = 0
    for t in trades:
        if t.pnl > 0:
            cur_wins += 1
            cur_losses = 0
            max_consec_wins = max(max_consec_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_consec_losses = max(max_consec_losses, cur_losses)

    # Equity curve — only from actual period (skip warmup bars)
    full_equity = result.equity_curve
    period_equity = full_equity[warmup_bar_count:]
    period_ohlcv = all_ohlcv[warmup_bar_count:]

    # Max drawdown from period equity curve
    max_dd = 0.0
    peak = period_equity[0] if period_equity else initial_capital
    dd_values: list[float] = []
    for eq in period_equity:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        dd_values.append(-dd)
        if dd > max_dd:
            max_dd = dd

    total_return_pct = total_pnl / initial_capital * 100
    final_equity = period_equity[-1] if period_equity else initial_capital

    # Compute risk metrics from period equity curve
    bar_ms = _TF_MS.get(req.timeframe, 3_600_000)
    periods_per_year = 365.25 * 24 * 3_600_000 / bar_ms

    eq_returns = []
    downside_returns = []
    for i in range(1, len(period_equity)):
        prev = period_equity[i - 1]
        if prev > 0:
            r = (period_equity[i] - prev) / prev
            eq_returns.append(r)
            if r < 0:
                downside_returns.append(r)

    if eq_returns:
        mean_r = sum(eq_returns) / len(eq_returns)
        var_r = sum((r - mean_r) ** 2 for r in eq_returns) / len(eq_returns)
        std_r = var_r**0.5 if var_r > 0 else 0.0
        sharpe_ratio = (mean_r / std_r) * (periods_per_year**0.5) if std_r > 0 else 0.0
        ann_vol = std_r * (periods_per_year**0.5) * 100

        # Sortino
        if downside_returns:
            down_var = sum(r**2 for r in downside_returns) / len(eq_returns)
            down_std = down_var**0.5
            sortino_ratio = (
                (mean_r / down_std) * (periods_per_year**0.5) if down_std > 0 else 0.0
            )
        else:
            sortino_ratio = 0.0

        # Annualized return
        n_periods = len(period_equity)
        if n_periods > 1 and final_equity > 0 and initial_capital > 0:
            ann_return = (
                (final_equity / initial_capital) ** (periods_per_year / n_periods) - 1
            ) * 100
        else:
            ann_return = 0.0

        calmar_ratio = ann_return / max_dd if max_dd > 0 else 0.0
        recovery_factor = total_return_pct / max_dd if max_dd > 0 else 0.0
    else:
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        ann_return = 0.0
        ann_vol = 0.0
        calmar_ratio = 0.0
        recovery_factor = 0.0

    # Build equity curve for frontend
    bar_count = len(period_equity)
    step = max(1, bar_count // 2000)
    bh_base_price = period_ohlcv[0][4] if period_ohlcv else 1.0
    equity_curve_out = []
    for i in range(0, bar_count, step):
        idx = min(i, len(period_ohlcv) - 1)
        ts = datetime.fromtimestamp(
            period_ohlcv[idx][0] / 1000, tz=timezone.utc
        ).isoformat()
        bh_val = initial_capital * period_ohlcv[idx][4] / bh_base_price
        equity_curve_out.append({"t": ts, "strategy": period_equity[i], "bh": bh_val})

    drawdown_curve_out = []
    for i in range(0, len(dd_values), step):
        idx = min(i, len(period_ohlcv) - 1)
        ts = datetime.fromtimestamp(
            period_ohlcv[idx][0] / 1000, tz=timezone.utc
        ).isoformat()
        drawdown_curve_out.append({"t": ts, "dd": dd_values[i]})

    # Trades
    trades_out = [
        TradeOut(
            timestamp=datetime.fromtimestamp(
                all_ohlcv[min(t.entry_bar, len(all_ohlcv) - 1)][0] / 1000,
                tz=timezone.utc,
            ).isoformat()
            if all_ohlcv
            else "",
            side="buy" if t.direction.value == "long" else "sell",
            price=t.entry_price,
            exit_price=t.exit_price,
            amount=abs(t.pnl / (t.exit_price - t.entry_price))
            if t.exit_price != t.entry_price
            else 0.0,
            fee=0.0,
            pnl=t.pnl,
            pnl_pct=t.pnl / initial_capital * 100,
            entry_time=datetime.fromtimestamp(
                all_ohlcv[min(t.entry_bar, len(all_ohlcv) - 1)][0] / 1000,
                tz=timezone.utc,
            ).isoformat(),
            exit_time=datetime.fromtimestamp(
                all_ohlcv[min(t.exit_bar, len(all_ohlcv) - 1)][0] / 1000,
                tz=timezone.utc,
            ).isoformat(),
            bars_held=t.exit_bar - t.entry_bar,
            mfe=t.mfe,
            mae=t.mae,
            mfe_pct=(t.mfe / (t.entry_price * t.qty) * 100)
            if t.entry_price > 0 and t.qty > 0
            else 0.0,
            mae_pct=(t.mae / (t.entry_price * t.qty) * 100)
            if t.entry_price > 0 and t.qty > 0
            else 0.0,
        )
        for t in trades
    ]

    return BacktestResultOut(
        total_return_pct=total_return_pct,
        bh_return_pct=(period_ohlcv[-1][4] / period_ohlcv[0][4] - 1) * 100
        if period_ohlcv
        else 0.0,
        annualized_return_pct=ann_return,
        max_drawdown_pct=max_dd,
        max_dd_duration_days=0.0,
        sharpe_ratio=sharpe_ratio,
        sharpe_ci_lo=None,
        sharpe_ci_hi=None,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        annualized_volatility_pct=ann_vol,
        recovery_factor=recovery_factor,
        total_trades=total,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        payoff_ratio=payoff_ratio,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=(avg_win * win_rate / 100 - avg_loss * (100 - win_rate) / 100)
        if total > 0
        else 0.0,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_consecutive_wins=max_consec_wins,
        max_consecutive_losses=max_consec_losses,
        avg_trade_duration_hours=0.0,
        final_equity=final_equity,
        initial_capital=initial_capital,
        equity_curve=equity_curve_out,
        drawdown_curve=drawdown_curve_out,
        monthly_returns=[],
        trades=trades_out,
        strategy=req.strategy or "pine_script",
        exchange=req.exchange,
        period_start=start_str,
        period_end=end_str,
        config_name="Pine Default",
    )


# ─── Optimize job ─────────────────────────────────────────────────────────────


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
    warmup_start = start_dt - timedelta(days=req.warmup_days)
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
            ast=ast, bars=train_bars, grid=grid, metric=req.metric
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
            ast=ast, bars=test_bars, grid=[best_params], metric=req.metric
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
    warmup_start = start_dt - timedelta(days=req.warmup_days)
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
        ast=ast, bars=s1_bars, grid=grid, metric=req.metric
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
            ast=ast, bars=test_bars, grid=[best_params], metric=req.metric
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
        ast=ast, bars=s3_bars, grid=[best_params], metric=req.metric
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


def _run_heatmap(req: OptimizeRequest) -> HeatmapResultOut:
    """Execute 2D parameter heatmap optimization."""
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        run_optimization,
    )
    from quantforge.pine.parser.parser import parse

    source = _resolve_pine_source(req.strategy, req.pine_source)
    ast = parse(source)

    inputs = extract_pine_inputs(ast)
    if len(inputs) < 2:
        raise ValueError(
            "Heatmap requires at least 2 input.int() / input.float() parameters"
        )

    # Take first 2 parameters for 2D heatmap
    x_param = inputs[0]
    y_param = inputs[1]

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(days=req.warmup_days)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)

    # Generate 2D grid
    def generate_param_range(param, resolution):
        lo = (
            param.minval
            if param.minval is not None
            else max(1, param.defval - abs(param.defval) * 0.5)
        )
        hi = (
            param.maxval
            if param.maxval is not None
            else param.defval + abs(param.defval) * 0.5
        )

        if param.input_type == "int":
            lo, hi = int(lo), int(hi)
            step = max(1, (hi - lo) // (resolution - 1))
            values = list(range(lo, hi + 1, step))
            if len(values) > resolution:
                values = values[:resolution]
        else:
            step = (hi - lo) / (resolution - 1) if resolution > 1 else 0
            values = [lo + i * step for i in range(resolution)]

        return values

    x_values = generate_param_range(x_param, req.resolution)
    y_values = generate_param_range(y_param, req.resolution)

    # Build 2D grid
    grid_2d = []
    for x_val in x_values:
        for y_val in y_values:
            params = {x_param.var_name: x_val, y_param.var_name: y_val}
            # Set other params to defaults
            for inp in inputs[2:]:
                params[inp.var_name] = inp.defval
            grid_2d.append(params)

    # Run optimization
    results = run_optimization(ast=ast, bars=bars, grid=grid_2d, metric="sharpe")

    # Build result grids
    sharpe_grid = [[None for _ in y_values] for _ in x_values]
    return_grid = [[None for _ in y_values] for _ in x_values]

    for result in results:
        x_val = result.params[x_param.var_name]
        y_val = result.params[y_param.var_name]

        try:
            x_idx = x_values.index(x_val)
            y_idx = y_values.index(y_val)
            sharpe_grid[x_idx][y_idx] = _safe_float(result.sharpe)
            return_grid[x_idx][y_idx] = _safe_float(result.return_pct * 100)
        except ValueError:
            continue  # Skip if value not in expected range

    # Find mesa regions (simplified: just find top performer)
    mesas = []
    best_result = max(results, key=lambda r: r.sharpe) if results else None
    if best_result:
        mesas.append(
            HeatmapMesaOut(
                index=0,
                center_x=best_result.params[x_param.var_name],
                center_y=best_result.params[y_param.var_name],
                avg_sharpe=_safe_float(best_result.sharpe),
                avg_return_pct=_safe_float(best_result.return_pct * 100),
                stability=1.0,  # Simplified
                area=1,
                frequency_label="Peak",
            )
        )

    return HeatmapResultOut(
        x_values=x_values,
        y_values=y_values,
        x_label=x_param.title,
        y_label=y_param.title,
        x_param=x_param.var_name,
        y_param=y_param.var_name,
        sharpe_grid=sharpe_grid,
        return_grid=return_grid,
        mesas=mesas,
    )


def _run_pine_optimize(req: OptimizeRequest) -> GridSearchResultOut:
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

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(days=req.warmup_days)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = _fetch_ohlcv(req.exchange, symbol, req.timeframe, since_ms, end_ms)
    bars = _ohlcv_to_bars(all_ohlcv)

    results = run_optimization(ast=ast, bars=bars, grid=grid, metric=req.metric)

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
            result = await asyncio.to_thread(_run_pine_optimize, req)
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
        elif req.mode == "heatmap":
            result = await asyncio.to_thread(_run_heatmap, req)
            check_cancelled(job_id)
            _jobs[job_id]["heatmap_result"] = result
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
