from __future__ import annotations

from pydantic import Field, model_validator

from quantforge.strategy import Strategy, StrategyConfig, register_strategy


class TslaNvdaOptionsConfig(StrategyConfig):
    entry_delta_min: float = Field(0.15, ge=0.05, le=0.50)
    entry_delta_max: float = Field(0.22, ge=0.05, le=0.50)
    dte_min: int = Field(21, ge=1, le=365)
    dte_max: int = Field(45, ge=1, le=730)
    profit_take: float = Field(0.70, ge=0.30, le=0.95)
    roll_delta: float = Field(0.50, ge=0.30, le=0.80)
    coverage_ratio: float = Field(0.50, ge=0, le=1)
    earnings_buffer_days: int = Field(7, ge=0, le=30)
    max_roll_debit_ratio: float = Field(0.30, ge=0, le=1)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.entry_delta_min > self.entry_delta_max:
            raise ValueError("entry_delta_min must not exceed entry_delta_max")
        if self.dte_min > self.dte_max:
            raise ValueError("dte_min must not exceed dte_max")
        return self


@register_strategy
class TslaNvdaOptionsManager(Strategy):
    """Daily TSLA/NVDA stock and options position manager."""

    name = "tsla_nvda_options"
    version = "1.0.0"
    config_model = TslaNvdaOptionsConfig

