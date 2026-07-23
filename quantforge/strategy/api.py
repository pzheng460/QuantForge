from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from quantforge.portfolio.ledger import PortfolioLedger


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    portfolio: PortfolioLedger
    market: Any
    environment: str


class Strategy:
    name = ""
    version = "1.0.0"
    config_model: type[StrategyConfig] = StrategyConfig

    def __init__(self, config: StrategyConfig):
        self.config = config

    def initialize(self, ctx: StrategyContext) -> None:
        pass

    def on_event(self, ctx: StrategyContext, event: Any) -> list[Any]:
        return []

    @classmethod
    def schema(cls) -> dict:
        return cls.config_model.model_json_schema()

