"""Python strategy backtest job runner."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from apps.dashboard.backend.http_errors import sanitize_exception
from apps.dashboard.backend.jobs.data import (
    _DEFAULT_SYMBOLS,
    _fetch_ohlcv,
    _resolve_date_range,
    check_bar_budget,
)
from apps.dashboard.backend.jobs.registry import (
    JobCancelled,
    check_cancelled,
    update_job,
)
from apps.dashboard.backend.models import (
    BacktestRequest,
    BacktestResultOut,
    TradeOut,
)
from quantforge.domain.timeframes import timeframe_to_ms, timeframe_to_seconds

logger = logging.getLogger(__name__)


def _approximate_earnings_calendar(start: date, end: date) -> tuple[date, ...]:
    """Quarterly (~91-day) anchors for the managed covered-call backtest,
    extended well past the period end.

    The managed model blocks entries when the NEXT earnings date is unknown —
    parity with the live OptionManager, which refuses all new covered calls
    without a known earnings date. A calendar that stops inside the period
    would therefore block the entire tail, so anchors continue past ``end``;
    within the period the strategy always has a known next report and only the
    dte+buffer days around each anchor are blocked, matching live behavior.
    """
    dates: list[date] = []
    day = start
    horizon = end + timedelta(days=182)
    while day <= horizon:
        dates.append(day)
        day += timedelta(days=91)
    return tuple(dates)


async def run_backtest_job(job_id: str, req: BacktestRequest) -> None:
    """Run a Python strategy backtest in the background and store the result."""
    update_job(job_id, status="running")

    try:
        result = await asyncio.to_thread(_run_python_backtest, req, job_id)
        check_cancelled(job_id)
        update_job(job_id, result=result, status="completed")

    except JobCancelled:
        update_job(job_id, status="cancelled")

    except Exception as exc:
        # Keep the full traceback server-side; the client only needs a stable
        # failure category (no raw exception text / workspace paths).
        logger.exception("Backtest job %s failed", job_id)
        update_job(
            job_id,
            status="failed",
            error=sanitize_exception(exc, prefix="backtest job failed"),
        )


def _run_python_backtest(
    req: BacktestRequest, job_id: str | None = None
) -> BacktestResultOut:
    """Execute a registered Python strategy synchronously.

    ``job_id`` powers cooperative cancellation: the data fetch checks between
    pages, the backtest engine every ``cancel_check_every`` bars, and the
    metric loops periodically — so a cancel stops a long run within a bounded
    number of bars instead of only after it finishes.
    """
    import quantforge.strategies  # noqa: F401
    from quantforge.backtest import BacktestConfig, run_backtest
    from quantforge.options.backtest import (
        ManagedCoveredCallConfig,
        run_managed_covered_call_approximation,
    )
    from quantforge.strategy import get_strategy
    from quantforge.strategy.bar import BarStrategy

    start_str, end_str = _resolve_date_range(req.period, req.start_date, req.end_date)
    check_bar_budget(req.timeframe, start_str, end_str)
    symbol = req.symbol or _DEFAULT_SYMBOLS.get(req.exchange, "BTC/USDT:USDT")

    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_seconds = timeframe_to_seconds(req.timeframe) * req.warmup_bars
    warmup_start = start_dt - timedelta(seconds=warmup_seconds)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    cancel = (lambda: check_cancelled(job_id)) if job_id else None
    all_ohlcv = _fetch_ohlcv(
        req.exchange, symbol, req.timeframe, since_ms, end_ms, cancel_check=cancel
    )
    # Find the bar index where actual backtest period starts (after warmup)
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_bar_count = 0
    for bar in all_ohlcv:
        if bar[0] >= start_ms:
            break
        warmup_bar_count += 1

    strategy_cls = get_strategy(req.strategy)
    initial_capital = req.position_size_usdt or 100_000
    if issubclass(strategy_cls, BarStrategy):
        result = run_backtest(
            strategy_cls,
            all_ohlcv,
            strategy_config=req.config_override,
            config=BacktestConfig(
                initial_capital=initial_capital,
                allocation_pct=getattr(strategy_cls, "allocation_pct", 1),
                cancel_check=cancel,
            ),
            warmup_bars=warmup_bar_count,
        )
        data_quality = "historical_market_data"
    elif req.strategy == "tsla_nvda_options":
        option_config = strategy_cls.config_model(**(req.config_override or {}))
        ticker = (req.symbol or "").upper().split(":")[0].split("/")[0]
        if ticker not in ("TSLA", "NVDA"):
            # The managed model prices per ticker and drives roll/delta/close
            # management like the live manager; refusals beat silently feeding
            # it arbitrary bars and pretending the result is a covered call.
            raise ValueError(
                "tsla_nvda_options backtest requires symbol TSLA or NVDA; "
                f"got {req.symbol or '(none)'}"
            )
        # Use the LIVE strategy's management knobs so the backtest exercises
        # the same decision engine the engine runs: roll_delta, profit_take,
        # earnings buffer and coverage all flow in. The full bar history
        # (warmup + period) feeds the 200-bar indicator warmup; evaluation
        # starts at the formal period, keeping the equity curve period-aligned.
        managed = run_managed_covered_call_approximation(
            ticker,
            all_ohlcv,
            initial_capital=initial_capital,
            config=ManagedCoveredCallConfig(
                minimum_core_shares=0,
                maximum_covered_ratio=option_config.coverage_ratio,
                dte_min=option_config.dte_min,
                dte_max=option_config.dte_max,
                profit_take_pct=option_config.profit_take,
                roll_delta=option_config.roll_delta,
                earnings_buffer_days=option_config.earnings_buffer_days,
            ),
            earnings_dates=_approximate_earnings_calendar(
                start_dt.date(), end_dt.date()
            ),
            evaluation_start=start_dt.date(),
        )
        result = managed.result
        if result is None:
            raise RuntimeError("managed covered-call backtest produced no result")
        # NOTE: all_ohlcv / warmup_bar_count are deliberately NOT rewritten:
        # the managed model's trade indices span the warmup+period bars and
        # must map back onto the full array for timestamps.
        data_quality = managed.quality
    else:
        raise ValueError(f"{req.strategy} does not provide a backtest adapter")
    trades = result.trades
    initial_capital = result.initial_capital

    # Recompute metrics on filtered trades
    total_pnl = sum(t.pnl for t in trades)
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl < 0)
    win_rate = (wins / total * 100) if total > 0 else 0.0

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = 0.0

    expectancy = total_pnl / total if total > 0 else 0.0
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
        elif t.pnl < 0:
            cur_losses += 1
            cur_wins = 0
            max_consec_losses = max(max_consec_losses, cur_losses)
        else:
            cur_wins = 0
            cur_losses = 0

    # Equity curve — only from actual period. Runtime trading state is reset
    # after warmup, so equity_curve already starts at the formal period.
    full_equity = result.equity_curve
    period_equity = full_equity
    period_ohlcv = all_ohlcv[warmup_bar_count:]
    if not period_ohlcv:
        raise ValueError("No OHLCV data in requested backtest period")
    if period_ohlcv and period_ohlcv[0][4] <= 0:
        raise ValueError("Period start close price must be positive")

    # Warmup bars are for indicator readiness, not part of the displayed
    # backtest period. Re-anchor strategy equity to the formal period start
    # so it uses the same baseline as buy-and-hold.
    if period_equity:
        period_base_equity = period_equity[0]
        if period_base_equity > 0:
            period_equity = [
                initial_capital * eq / period_base_equity for eq in period_equity
            ]

    # Max drawdown from period equity curve
    max_dd = 0.0
    peak = period_equity[0] if period_equity else initial_capital
    peak_idx = 0
    underwater_start_idx: int | None = None
    max_dd_duration_days = 0.0
    dd_values: list[float] = []
    bar_ms = timeframe_to_ms(req.timeframe)
    bar_days = bar_ms / (24 * 3_600_000)
    for i, eq in enumerate(period_equity):
        if cancel and i % 256 == 0:
            cancel()
        if eq > peak:
            peak = eq
            peak_idx = i
            underwater_start_idx = None
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        dd_values.append(-dd)
        if dd > max_dd:
            max_dd = dd
        if dd > 0:
            if underwater_start_idx is None:
                underwater_start_idx = peak_idx
            max_dd_duration_days = max(
                max_dd_duration_days,
                (i - underwater_start_idx) * bar_days,
            )
        else:
            underwater_start_idx = None

    final_equity = period_equity[-1] if period_equity else initial_capital
    total_return_pct = (final_equity / initial_capital - 1) * 100

    # Compute risk metrics from period equity curve
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
        return_intervals = len(period_equity) - 1
        if return_intervals > 0 and final_equity > 0 and initial_capital > 0:
            ann_return = (
                (final_equity / initial_capital)
                ** (periods_per_year / return_intervals)
                - 1
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
    max_curve_points = 2000
    step = max(1, (bar_count + max_curve_points - 1) // max_curve_points)
    bh_base_price = period_ohlcv[0][4] if period_ohlcv else 1.0
    sampled_indexes = list(range(0, bar_count, step))
    if bar_count and sampled_indexes[-1] != bar_count - 1:
        if len(sampled_indexes) < max_curve_points:
            sampled_indexes.append(bar_count - 1)
        else:
            sampled_indexes[-1] = bar_count - 1
    equity_curve_out = []
    for i in sampled_indexes:
        idx = min(i, len(period_ohlcv) - 1)
        ts = datetime.fromtimestamp(
            period_ohlcv[idx][0] / 1000, tz=timezone.utc
        ).isoformat()
        bh_val = initial_capital * period_ohlcv[idx][4] / bh_base_price
        equity_curve_out.append({"t": ts, "strategy": period_equity[i], "bh": bh_val})

    drawdown_curve_out = []
    for i in sampled_indexes:
        idx = min(i, len(period_ohlcv) - 1)
        ts = datetime.fromtimestamp(
            period_ohlcv[idx][0] / 1000, tz=timezone.utc
        ).isoformat()
        drawdown_curve_out.append({"t": ts, "dd": dd_values[i]})

    def _trade_time_iso(bar_index: int) -> str:
        if not all_ohlcv:
            return ""
        if bar_index < len(all_ohlcv):
            ts_ms = all_ohlcv[max(bar_index, 0)][0]
        else:
            overflow_bars = bar_index - (len(all_ohlcv) - 1)
            ts_ms = all_ohlcv[-1][0] + overflow_bars * bar_ms
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

    def _trade_pnl_pct(trade) -> float:
        notional = trade.entry_price * abs(trade.quantity)
        return trade.pnl / notional * 100 if notional > 0 else 0.0

    # Trades
    trades_out = [
        TradeOut(
            timestamp=_trade_time_iso(t.entry_bar),
            side="buy" if t.direction == "long" else "sell",
            price=t.entry_price,
            exit_price=t.exit_price,
            amount=abs(t.quantity),
            fee=t.fee,
            pnl=t.pnl,
            pnl_pct=_trade_pnl_pct(t),
            entry_time=_trade_time_iso(t.entry_bar),
            exit_time=_trade_time_iso(t.exit_bar),
            bars_held=t.exit_bar - t.entry_bar,
            mfe=t.mfe,
            mae=t.mae,
            mfe_pct=(t.mfe / (t.entry_price * t.quantity) * 100)
            if t.entry_price > 0 and t.quantity > 0
            else 0.0,
            mae_pct=(t.mae / (t.entry_price * t.quantity) * 100)
            if t.entry_price > 0 and t.quantity > 0
            else 0.0,
        )
        for t in trades
    ]

    avg_trade_duration_hours = (
        sum((t.exit_bar - t.entry_bar) * bar_ms / 3_600_000 for t in trades) / total
        if total > 0
        else 0.0
    )

    monthly_buckets: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for i, eq in enumerate(period_equity):
        if i >= len(period_ohlcv):
            break
        dt = datetime.fromtimestamp(period_ohlcv[i][0] / 1000, tz=timezone.utc)
        monthly_buckets.setdefault((dt.year, dt.month), []).append((i, eq))

    monthly_returns = []
    prev_month_last: float | None = None
    for (year, month), values in sorted(monthly_buckets.items()):
        first = values[0][1]
        last = values[-1][1]
        base = prev_month_last if prev_month_last is not None else first
        ret = (last / base - 1) * 100 if base > 0 else 0.0
        monthly_returns.append({"year": year, "month": month, "return": ret})
        prev_month_last = last

    return BacktestResultOut(
        total_return_pct=total_return_pct,
        bh_return_pct=(period_ohlcv[-1][4] / period_ohlcv[0][4] - 1) * 100
        if period_ohlcv
        else 0.0,
        annualized_return_pct=ann_return,
        max_drawdown_pct=max_dd,
        max_dd_duration_days=max_dd_duration_days,
        sharpe_ratio=sharpe_ratio,
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
        expectancy=expectancy,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_consecutive_wins=max_consec_wins,
        max_consecutive_losses=max_consec_losses,
        avg_trade_duration_hours=avg_trade_duration_hours,
        final_equity=final_equity,
        initial_capital=initial_capital,
        equity_curve=equity_curve_out,
        drawdown_curve=drawdown_curve_out,
        monthly_returns=monthly_returns,
        trades=trades_out,
        strategy=req.strategy,
        exchange=req.exchange,
        period_start=start_str,
        period_end=end_str,
        config_name=(
            f"Python {strategy_cls.version}"
            if data_quality == "historical_market_data"
            else f"Python {strategy_cls.version} ({data_quality})"
        ),
        data_quality=data_quality,
    )
