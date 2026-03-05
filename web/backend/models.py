"""Pydantic request/response models for the backtest API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, field_validator

_VALID_PERIODS = {"1w", "1m", "3m", "6m", "1y", "2y", "3y", "5y"}
_VALID_EXCHANGES = {"bitget", "binance", "okx", "bybit", "hyperliquid"}
_VALID_MODES = {"grid", "wfo", "full", "heatmap"}


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

    @field_validator("exchange")
    @classmethod
    def validate_exchange(cls, v: str) -> str:
        if v not in _VALID_EXCHANGES:
            raise ValueError(f"exchange must be one of {_VALID_EXCHANGES}")
        return v

    @field_validator("period")
    @classmethod
    def validate_period(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_PERIODS:
            raise ValueError(f"period must be one of {_VALID_PERIODS}")
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        if not (0.1 <= v <= 50):
            raise ValueError("leverage must be between 0.1 and 50")
        return v

    @field_validator("mesa_index")
    @classmethod
    def validate_mesa_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("mesa_index must be >= 0")
        return v


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


# ─── Optimizer models ────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    strategy: str
    exchange: str = "bitget"
    symbol: Optional[str] = None
    period: Optional[str] = "1y"
    start_date: Optional[str] = None   # YYYY-MM-DD
    end_date: Optional[str] = None     # YYYY-MM-DD
    leverage: float = 1.0
    mode: str = "grid"                 # grid | wfo | full | heatmap
    n_jobs: int = 1
    resolution: int = 15               # heatmap grid resolution

    @field_validator("exchange")
    @classmethod
    def validate_exchange(cls, v: str) -> str:
        if v not in _VALID_EXCHANGES:
            raise ValueError(f"exchange must be one of {_VALID_EXCHANGES}")
        return v

    @field_validator("period")
    @classmethod
    def validate_period(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_PERIODS:
            raise ValueError(f"period must be one of {_VALID_PERIODS}")
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        if not (0.1 <= v <= 50):
            raise ValueError("leverage must be between 0.1 and 50")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}")
        return v

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, v: int) -> int:
        if not (3 <= v <= 50):
            raise ValueError("resolution must be between 3 and 50")
        return v


class GridRowOut(BaseModel):
    rank: int
    params: Dict[str, Any]
    sharpe: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float


class GridSearchResultOut(BaseModel):
    best_params: Dict[str, Any]
    best_sharpe: float
    best_return_pct: float
    best_drawdown_pct: float
    rows: List[GridRowOut]
    train_start: str
    train_end: str


class WFOWindowOut(BaseModel):
    window: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: Dict[str, Any]
    train_sharpe: float
    train_return_pct: float
    test_sharpe: float
    test_return_pct: float
    test_drawdown_pct: float


class WFOResultOut(BaseModel):
    windows: List[WFOWindowOut]
    windows_count: int
    avg_train_return: float
    avg_test_return: float
    robustness_ratio: float
    positive_windows: int
    total_test_return: float


class ThreeStageResultOut(BaseModel):
    best_params: Dict[str, Any]
    # Stage 1
    s1_in_sample_return: float
    s1_in_sample_sharpe: float
    s1_in_sample_drawdown: float
    s1_in_sample_trades: int
    s1_pass: bool
    # Stage 2
    s2_windows_count: int
    s2_avg_train_return: float
    s2_avg_test_return: float
    s2_robustness_ratio: float
    s2_positive_windows: int
    s2_total_test_return: float
    s2_pass: bool
    # Stage 3
    s3_holdout_return: float
    s3_bh_return: float
    s3_holdout_sharpe: float
    s3_sharpe_ci_lo: Optional[float]
    s3_sharpe_ci_hi: Optional[float]
    s3_holdout_drawdown: float
    s3_holdout_trades: int
    s3_holdout_win_rate: float
    s3_degradation: float
    s3_pass: bool
    # Summary
    all_pass: bool
    bh_full_return: float


class HeatmapMesaOut(BaseModel):
    index: int
    center_x: float
    center_y: float
    avg_sharpe: float
    avg_return_pct: float
    stability: float
    area: int
    frequency_label: str


class HeatmapResultOut(BaseModel):
    x_values: List[float]
    y_values: List[float]
    x_label: str
    y_label: str
    x_param: str
    y_param: str
    sharpe_grid: List[List[Optional[float]]]
    return_grid: List[List[Optional[float]]]
    mesas: List[HeatmapMesaOut]


class OptimizeJobStatusOut(BaseModel):
    job_id: str
    status: str
    error: Optional[str] = None
    mode: Optional[str] = None
    grid_result: Optional[GridSearchResultOut] = None
    wfo_result: Optional[WFOResultOut] = None
    full_result: Optional[ThreeStageResultOut] = None
    heatmap_result: Optional[HeatmapResultOut] = None


# ─── Live monitoring models ─────────────────────────────────────────────────

class LiveTradeOut(BaseModel):
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    amount: float
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    exit_reason: str = ""


class LivePerformanceOut(BaseModel):
    # Session info
    start_time: str = ""
    last_update: str = ""
    mesa_index: int = 0
    config_name: str = ""
    # Balance
    initial_balance: float = 0.0
    current_balance: float = 0.0
    peak_balance: float = 0.0
    # Performance
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    # Trades
    trades: List[LiveTradeOut] = []


class LiveStrategyStatusOut(BaseModel):
    strategy: str
    display_name: str
    is_active: bool
    performance: Optional[LivePerformanceOut] = None
