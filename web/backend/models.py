"""Pydantic request/response models for the backtest API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class BacktestRequest(BaseModel):
    strategy: str
    exchange: str = "bitget"
    symbol: Optional[str] = None
    period: Optional[str] = "1y"
    start_date: Optional[str] = None   # YYYY-MM-DD
    end_date: Optional[str] = None     # YYYY-MM-DD
    leverage: float = 1.0
    mesa_index: int = 0
    config_override: Optional[Dict[str, Any]] = None
    filter_override: Optional[Dict[str, Any]] = None


class TradeOut(BaseModel):
    timestamp: str
    side: str
    price: float
    amount: float
    fee: float
    pnl: float
    pnl_pct: float


class BacktestResultOut(BaseModel):
    # Returns
    total_return_pct: float
    bh_return_pct: float
    annualized_return_pct: float
    # Risk
    max_drawdown_pct: float
    sharpe_ratio: float
    sharpe_ci_lo: Optional[float]
    sharpe_ci_hi: Optional[float]
    sortino_ratio: float
    calmar_ratio: float
    # Trade stats
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy: float
    largest_win: float
    largest_loss: float
    final_equity: float
    # Curves: list of {"t": iso_str, "strategy": float, "bh": float}
    equity_curve: List[Dict[str, Any]]
    # Drawdown curve: list of {"t": iso_str, "dd": float}
    drawdown_curve: List[Dict[str, Any]]
    # Monthly returns: list of {"year": int, "month": int, "return": float}
    monthly_returns: List[Dict[str, Any]]
    # Trades
    trades: List[TradeOut]
    # Meta
    strategy: str
    exchange: str
    period_start: str
    period_end: str
    config_name: str


class JobStatusOut(BaseModel):
    job_id: str
    status: str          # pending | running | completed | failed
    error: Optional[str] = None
    result: Optional[BacktestResultOut] = None


class SchemaField(BaseModel):
    name: str
    type: str            # float | int | str | bool
    default: Any
    label: str           # human-readable label
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


class StrategySchema(BaseModel):
    name: str
    display_name: str
    default_interval: str
    config_fields: List[SchemaField]
    filter_fields: List[SchemaField]
