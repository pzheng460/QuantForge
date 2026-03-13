"""Pine Script API router — parse, backtest, and transpile Pine scripts."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/pine", tags=["pine"])


# --- Request / Response models ---


class PineParseRequest(BaseModel):
    pine_source: str


class PineParseResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    statement_count: int = 0
    has_strategy: bool = False


class PineBacktestRequest(BaseModel):
    pine_source: str
    symbol: str = "BTC/USDT:USDT"
    exchange: str = "bitget"
    timeframe: str = "15m"
    start: str = "2026-01-01"
    end: str = "2026-03-12"
    warmup_days: int = 60


class PineTradeOut(BaseModel):
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    entry_bar: int
    exit_bar: int
    comment_entry: str = ""
    comment_exit: str = ""


class PineMetrics(BaseModel):
    initial_capital: float
    final_equity: float
    net_pnl: float
    return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float


class PineBacktestResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    metrics: Optional[PineMetrics] = None
    trades: list[PineTradeOut] = []
    equity_curve: list[float] = []


class PineTranspileRequest(BaseModel):
    pine_source: str


class PineTranspileResponse(BaseModel):
    success: bool
    python_code: str = ""
    error: Optional[str] = None


# --- Endpoints ---


@router.post("/parse", response_model=PineParseResponse)
async def parse_pine(req: PineParseRequest) -> PineParseResponse:
    """Parse Pine Script source and validate syntax."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.parser.ast_nodes import StrategyDecl

        ast = parse(req.pine_source)
        has_strategy = any(isinstance(d, StrategyDecl) for d in ast.declarations)
        return PineParseResponse(
            valid=True,
            statement_count=len(ast.body),
            has_strategy=has_strategy,
        )
    except Exception as e:
        return PineParseResponse(valid=False, error=str(e))


@router.post("/backtest", response_model=PineBacktestResponse)
async def backtest_pine(req: PineBacktestRequest) -> PineBacktestResponse:
    """Run Pine Script strategy backtest."""
    import asyncio

    try:
        result = await asyncio.to_thread(_run_pine_backtest, req)
        return result
    except Exception as e:
        return PineBacktestResponse(success=False, error=str(e))


def _run_pine_backtest(req: PineBacktestRequest) -> PineBacktestResponse:
    """CPU-bound backtest execution."""
    from quantforge.pine.parser.parser import parse
    from quantforge.pine.interpreter.context import BarData, ExecutionContext
    from quantforge.pine.interpreter.runtime import PineRuntime

    ast = parse(req.pine_source)

    # Fetch data via ccxt
    import ccxt
    from datetime import datetime, timezone, timedelta

    exchange_cls = getattr(ccxt, req.exchange, None)
    if exchange_cls is None:
        return PineBacktestResponse(
            success=False, error=f"Unknown exchange: {req.exchange}"
        )

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(days=req.warmup_days)

    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv: list[list] = []
    current_since = since_ms
    while current_since < end_ms:
        ohlcv = exchange.fetch_ohlcv(
            req.symbol, req.timeframe, since=current_since, limit=1000
        )
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

    all_ohlcv = [bar for bar in all_ohlcv if bar[0] <= end_ms]

    if not all_ohlcv:
        return PineBacktestResponse(success=False, error="No OHLCV data returned")

    bars = [
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

    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)

    # Build response
    trades_out = [
        PineTradeOut(
            direction=t.direction.value,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            pnl=t.pnl,
            entry_bar=int(t.entry_bar),
            exit_bar=int(t.exit_bar),
            comment_entry=t.comment_entry,
            comment_exit=t.comment_exit,
        )
        for t in result.trades
    ]

    gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in result.trades if t.pnl <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    max_dd = 0.0
    peak = result.initial_capital
    for eq in result.equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    final_equity = (
        result.equity_curve[-1] if result.equity_curve else result.initial_capital
    )
    net_pnl = result.net_profit

    # Downsample equity curve for frontend (max 500 points)
    eq = result.equity_curve
    if len(eq) > 500:
        step = len(eq) / 500
        eq = [eq[int(i * step)] for i in range(500)]

    metrics = PineMetrics(
        initial_capital=result.initial_capital,
        final_equity=final_equity,
        net_pnl=net_pnl,
        return_pct=net_pnl / result.initial_capital if result.initial_capital else 0,
        total_trades=result.total_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        profit_factor=profit_factor if profit_factor != float("inf") else 999.99,
        max_drawdown=max_dd,
    )

    return PineBacktestResponse(
        success=True,
        metrics=metrics,
        trades=trades_out,
        equity_curve=eq,
    )


class PineOptimizeRequest(BaseModel):
    pine_source: str
    symbol: str = "BTC/USDT:USDT"
    exchange: str = "bitget"
    timeframe: str = "15m"
    start: str = "2026-01-01"
    end: str = "2026-03-12"
    warmup_days: int = 60
    metric: str = "sharpe"


class PineInputOut(BaseModel):
    var_name: str
    title: str
    input_type: str
    defval: float
    minval: Optional[float] = None
    maxval: Optional[float] = None
    step: Optional[float] = None


class PineOptResultOut(BaseModel):
    params: dict[str, float]
    sharpe: float
    return_pct: float
    net_profit: float
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float


class PineOptimizeResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    inputs: list[PineInputOut] = []
    results: list[PineOptResultOut] = []
    total_combinations: int = 0


@router.post("/optimize", response_model=PineOptimizeResponse)
async def optimize_pine(req: PineOptimizeRequest) -> PineOptimizeResponse:
    """Run grid search optimization over Pine Script input parameters."""
    import asyncio

    try:
        result = await asyncio.to_thread(_run_pine_optimize, req)
        return result
    except Exception as e:
        return PineOptimizeResponse(success=False, error=str(e))


def _run_pine_optimize(req: PineOptimizeRequest) -> PineOptimizeResponse:
    """CPU-bound optimization execution."""
    from quantforge.pine.interpreter.context import BarData
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        generate_grid,
        run_optimization,
    )
    from quantforge.pine.parser.parser import parse

    ast = parse(req.pine_source)

    inputs = extract_pine_inputs(ast)
    if not inputs:
        return PineOptimizeResponse(
            success=False, error="No input.int() / input.float() parameters found"
        )

    grid = generate_grid(inputs)

    # Fetch data via ccxt
    import ccxt
    from datetime import datetime, timedelta, timezone

    exchange_cls = getattr(ccxt, req.exchange, None)
    if exchange_cls is None:
        return PineOptimizeResponse(
            success=False, error=f"Unknown exchange: {req.exchange}"
        )

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    start_dt = datetime.strptime(req.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(req.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = start_dt - timedelta(days=req.warmup_days)

    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv: list[list] = []
    current_since = since_ms
    while current_since < end_ms:
        ohlcv = exchange.fetch_ohlcv(
            req.symbol, req.timeframe, since=current_since, limit=1000
        )
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

    all_ohlcv = [bar for bar in all_ohlcv if bar[0] <= end_ms]

    if not all_ohlcv:
        return PineOptimizeResponse(success=False, error="No OHLCV data returned")

    bars = [
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

    results = run_optimization(ast=ast, bars=bars, grid=grid, metric=req.metric)

    inputs_out = [
        PineInputOut(
            var_name=inp.var_name,
            title=inp.title,
            input_type=inp.input_type,
            defval=inp.defval,
            minval=inp.minval,
            maxval=inp.maxval,
            step=inp.step,
        )
        for inp in inputs
    ]

    results_out = [
        PineOptResultOut(
            params=r.params,
            sharpe=r.sharpe,
            return_pct=r.return_pct,
            net_profit=r.net_profit,
            total_trades=r.total_trades,
            win_rate=r.win_rate,
            profit_factor=r.profit_factor,
            max_drawdown=r.max_drawdown,
        )
        for r in results
    ]

    return PineOptimizeResponse(
        success=True,
        inputs=inputs_out,
        results=results_out,
        total_combinations=len(grid),
    )


@router.post("/transpile", response_model=PineTranspileResponse)
async def transpile_pine(req: PineTranspileRequest) -> PineTranspileResponse:
    """Transpile Pine Script to QuantForge Python code."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.transpiler.codegen import transpile

        ast = parse(req.pine_source)
        python_code = transpile(ast)
        return PineTranspileResponse(success=True, python_code=python_code)
    except Exception as e:
        return PineTranspileResponse(success=False, error=str(e))
