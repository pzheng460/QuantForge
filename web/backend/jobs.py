"""Background job management for backtest tasks."""

from __future__ import annotations

import asyncio
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


async def run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """Run a Pine Script backtest in the background and store the result."""
    _jobs[job_id]["status"] = "running"

    try:
        result = await asyncio.to_thread(_run_pine_backtest, req)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = "completed"

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
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
    "1d": 86_400_000, "1w": 604_800_000,
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
        ohlcv = exchange.fetch_ohlcv(
            symbol, timeframe, since=current, limit=batch_size
        )
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
        equity_curve_out.append(
            {"t": ts, "strategy": period_equity[i], "bh": bh_val}
        )

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
        annualized_return_pct=0.0,
        max_drawdown_pct=max_dd,
        max_dd_duration_days=0.0,
        sharpe_ratio=0.0,
        sharpe_ci_lo=None,
        sharpe_ci_hi=None,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        annualized_volatility_pct=0.0,
        recovery_factor=0.0,
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
        grid_result = await asyncio.to_thread(_run_pine_optimize, req)
        _jobs[job_id]["grid_result"] = grid_result
        _jobs[job_id]["status"] = "completed"  # status AFTER result to avoid race

    except Exception as exc:
        import traceback

        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = (
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
