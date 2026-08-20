"""Pydantic request/response models for the backtest API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

_VALID_PERIODS = {"1w", "1m", "3m", "6m", "1y", "2y", "3y", "5y"}
_VALID_EXCHANGES = {"bitget", "binance", "okx", "bybit", "hyperliquid", "schwab"}
_VALID_MODES = {"grid", "wfo", "full"}

#: Server-side hard ceilings for live-trading risk parameters. Limits are set
#: by the operator, but a request may NEVER raise them past these absolute
#: bounds — the same non-bypassable philosophy as the run-once options path.
MAX_ORDER_NOTIONAL_USD = 100_000
MAX_LEVERAGE = 50
MAX_DAILY_NEW_POSITIONS = 50
MAX_POSITION_SIZE_USD = 1_000_000
_VALID_TIMEFRAMES = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "12h",
    "1d",
    "1w",
}


def _parse_date(value: str, field_name: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc


# Upper bound on an explicit backtest/optimize date span. The frame-aware
# bar-count ceiling (jobs.data.MAX_BACKTEST_BARS) is the real anti-DoS guard;
# this catches absurd spans at validation time with a clean 422.
_MAX_DATE_SPAN_DAYS = 10 * 365


def _validate_date_range(start_date: Optional[str], end_date: Optional[str]) -> None:
    if start_date:
        start_dt = _parse_date(start_date, "start_date")
        end_dt = (
            _parse_date(end_date, "end_date")
            if end_date
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )
        if start_dt >= end_dt:
            raise ValueError("start_date must be before end_date")
        if (end_dt - start_dt).days > _MAX_DATE_SPAN_DAYS:
            raise ValueError(
                f"date range must not exceed {_MAX_DATE_SPAN_DAYS} days"
            )
    elif end_date:
        _parse_date(end_date, "end_date")


class BacktestRequest(BaseModel):
    strategy: str
    exchange: str = "bitget"
    symbol: Optional[str] = None
    timeframe: str = "1h"
    period: Optional[str] = "1y"
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD
    leverage: float = 1.0
    warmup_bars: int = 500
    # Optional capital allocation for this strategy run.
    position_size_usdt: Optional[float] = None
    config_override: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def check_request(self):
        if not self.strategy.strip():
            raise ValueError("strategy is required")
        _validate_date_range(self.start_date, self.end_date)
        return self

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

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        if v not in _VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {_VALID_TIMEFRAMES}")
        return v

    @field_validator("warmup_bars")
    @classmethod
    def validate_warmup_bars(cls, v: int) -> int:
        if not (0 <= v <= 10000):
            raise ValueError("warmup_bars must be between 0 and 10000")
        return v

    @field_validator("position_size_usdt")
    @classmethod
    def validate_position_size_usdt(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("position_size_usdt must be positive")
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        if not (0.1 <= v <= 50):
            raise ValueError("leverage must be between 0.1 and 50")
        return v


class TradeOut(BaseModel):
    timestamp: str
    side: str
    price: float
    exit_price: float = 0.0
    amount: float
    fee: float
    pnl: float
    pnl_pct: float
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    bars_held: Optional[int] = None
    mfe: Optional[float] = None  # Maximum Favorable Excursion (USDT)
    mae: Optional[float] = None  # Maximum Adverse Excursion (USDT, negative)
    mfe_pct: Optional[float] = None  # MFE as % of position value
    mae_pct: Optional[float] = None  # MAE as % of position value


class BacktestResultOut(BaseModel):
    # Returns
    total_return_pct: float
    bh_return_pct: float
    annualized_return_pct: float
    # Risk
    max_drawdown_pct: float
    max_dd_duration_days: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    annualized_volatility_pct: float
    recovery_factor: float
    # Trade stats
    total_trades: int
    win_rate_pct: float
    profit_factor: Optional[float]
    payoff_ratio: float
    avg_win: float
    avg_loss: float
    expectancy: float
    largest_win: float
    largest_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_trade_duration_hours: float
    final_equity: float
    initial_capital: float = 100000.0
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
    data_quality: str = "historical_market_data"


class JobStatusOut(BaseModel):
    job_id: str
    status: str  # pending | running | completed | failed
    error: Optional[str] = None
    result: Optional[BacktestResultOut] = None


class SchemaField(BaseModel):
    name: str
    type: str  # float | int | str | bool
    default: Any
    label: str  # human-readable label
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None


# ─── Optimizer models ────────────────────────────────────────────────────────


class OptimizeRequest(BaseModel):
    strategy: str
    exchange: str = "bitget"
    symbol: Optional[str] = None
    timeframe: str = "1h"
    period: Optional[str] = "1y"
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD
    leverage: float = 1.0
    warmup_bars: int = 500
    # Optional sizing override — see BacktestRequest for semantics.
    position_size_usdt: Optional[float] = None
    metric: str = "sharpe"
    mode: str = "grid"  # grid | wfo | full

    @model_validator(mode="after")
    def check_request(self):
        if not self.strategy.strip():
            raise ValueError("strategy is required")
        _validate_date_range(self.start_date, self.end_date)
        return self

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

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, v: str) -> str:
        if v not in _VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {_VALID_TIMEFRAMES}")
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        if not (0.1 <= v <= 50):
            raise ValueError("leverage must be between 0.1 and 50")
        return v

    @field_validator("warmup_bars")
    @classmethod
    def validate_warmup_bars(cls, v: int) -> int:
        if not (0 <= v <= 10000):
            raise ValueError("warmup_bars must be between 0 and 10000")
        return v

    @field_validator("position_size_usdt")
    @classmethod
    def validate_position_size_usdt(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("position_size_usdt must be positive")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}")
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
    s3_holdout_drawdown: float
    s3_holdout_trades: int
    s3_holdout_win_rate: float
    s3_degradation: float
    s3_pass: bool
    # Summary
    all_pass: bool
    bh_full_return: float


class OptimizeProgress(BaseModel):
    completed: int
    total: int
    # Average wall-clock seconds per evaluated combination so far.
    # Frontend computes ETA = (total - completed) * avg_secs_per_combo.
    avg_secs_per_combo: Optional[float] = None
    elapsed_secs: Optional[float] = None


class OptimizeJobStatusOut(BaseModel):
    job_id: str
    status: str
    error: Optional[str] = None
    mode: Optional[str] = None
    progress: Optional[OptimizeProgress] = None
    grid_result: Optional[GridSearchResultOut] = None
    wfo_result: Optional[WFOResultOut] = None
    full_result: Optional[ThreeStageResultOut] = None


# ─── Live engine management models ───────────────────────────────────────────


class LiveStartRequest(BaseModel):
    strategy: str
    exchange: str = "bitget"
    symbol: Optional[str] = None
    timeframe: str = "1h"
    demo: bool = True
    position_size_usdt: float = Field(default=100.0, gt=0, le=MAX_POSITION_SIZE_USD)
    leverage: int = Field(default=1, ge=1, le=MAX_LEVERAGE)
    warmup_bars: int = 500
    config_override: Optional[Dict[str, Any]] = None
    max_order_notional: float = Field(
        default=10_000, gt=0, le=MAX_ORDER_NOTIONAL_USD
    )
    max_spread_pct: float = Field(default=0.15, gt=0, le=1)
    max_leverage: float = Field(default=3, ge=1, le=MAX_LEVERAGE)
    max_daily_new_positions: int = Field(
        default=10, ge=1, le=MAX_DAILY_NEW_POSITIONS
    )

    @model_validator(mode="after")
    def validate_live_broker(self):
        valid = _VALID_EXCHANGES | {"schwab"}
        if self.exchange not in valid:
            raise ValueError(f"exchange must be one of {valid}")
        if self.exchange == "schwab":
            if self.leverage != 1:
                raise ValueError("Schwab equities require leverage=1")
            if self.timeframe not in {"1m", "5m", "15m", "30m", "1h", "1d", "1w"}:
                raise ValueError("Unsupported Schwab timeframe")
        return self


class LiveEngineOut(BaseModel):
    engine_id: str
    status: str  # warmup | running | stopped | failed
    strategy: str = ""
    exchange: str = ""
    symbol: str = ""
    timeframe: str = ""
    demo: bool = True
    leverage: int = 1
    created_at: str = ""
    # Populated when engine transitions to stopped (history entry).
    stopped_at: Optional[str] = None
    error: Optional[str] = None
