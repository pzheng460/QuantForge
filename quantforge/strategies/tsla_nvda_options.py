from __future__ import annotations

from pydantic import Field, model_validator

from quantforge.strategy import Strategy, StrategyConfig, register_strategy
from quantforge.options.manager import OptionManager, OptionManagerInput


class TslaNvdaOptionsConfig(StrategyConfig):
    entry_delta_min: float = Field(0.15, ge=0.05, le=0.50)
    entry_delta_max: float = Field(0.22, ge=0.05, le=0.50)
    dte_min: int = Field(21, ge=1, le=365)
    dte_max: int = Field(45, ge=1, le=730)
    profit_take: float = Field(0.70, ge=0.30, le=0.95)
    #: Delta threshold at which an open short call is managed (bought back).
    roll_delta: float = Field(0.50, ge=0.30, le=0.80)
    #: Optional upper bound on the covered fraction of the position applied on
    #: top of the per-run maximum_covered_ratio (the tighter of the two wins).
    #: Default 1.0 means "no cap" — identical to the pre-wiring behavior, and
    #: only an explicit value below 1.0 restricts coverage.
    coverage_ratio: float = Field(1.0, ge=0, le=1)
    earnings_buffer_days: int = Field(7, ge=0, le=30)

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

    def on_event(self, ctx, event):
        if not isinstance(event, OptionManagerInput):
            return []
        manager = OptionManager(
            dte_min=self.config.dte_min,
            dte_max=self.config.dte_max,
            delta_min=self.config.entry_delta_min,
            delta_max=self.config.entry_delta_max,
            earnings_buffer_days=self.config.earnings_buffer_days,
            profit_take=self.config.profit_take,
            roll_delta=self.config.roll_delta,
            maximum_covered_ratio=self.config.coverage_ratio,
        )
        return [manager.evaluate(event)]
