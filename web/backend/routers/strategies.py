"""Strategy listing and schema endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from strategy.backtest.registry import list_strategies, get_strategy
from strategy.backtest.exchange_profiles import list_exchanges, get_profile
from web.backend.models import StrategySchema
from web.backend.schema_gen import dataclass_to_fields

router = APIRouter()


@router.get("/strategies", response_model=List[StrategySchema])
def get_strategies() -> List[StrategySchema]:
    """Return all registered strategies with their parameter schemas."""
    result = []
    for name in list_strategies():
        try:
            reg = get_strategy(name)
            config_fields = dataclass_to_fields(reg.config_cls)
            filter_fields = dataclass_to_fields(reg.filter_config_cls)
            result.append(
                StrategySchema(
                    name=name,
                    display_name=reg.display_name,
                    default_interval=reg.default_interval.value,
                    config_fields=config_fields,
                    filter_fields=filter_fields,
                )
            )
        except Exception:
            pass
    return result


@router.get("/strategies/{name}", response_model=StrategySchema)
def get_strategy_schema(name: str) -> StrategySchema:
    """Return parameter schema for a single strategy."""
    try:
        reg = get_strategy(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    return StrategySchema(
        name=name,
        display_name=reg.display_name,
        default_interval=reg.default_interval.value,
        config_fields=dataclass_to_fields(reg.config_cls),
        filter_fields=dataclass_to_fields(reg.filter_config_cls),
    )


@router.get("/exchanges")
def get_exchanges():
    """Return list of supported exchanges with their default symbols."""
    result = []
    for ex in list_exchanges():
        try:
            p = get_profile(ex)
            result.append(
                {
                    "id": ex,
                    "name": p.name,
                    "default_symbol": p.default_symbol,
                    "maker_fee": p.cost_config().maker_fee,
                    "taker_fee": p.cost_config().taker_fee,
                }
            )
        except Exception:
            pass
    return result
