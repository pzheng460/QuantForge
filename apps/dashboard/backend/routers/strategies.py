"""Read-only Python strategy registry and configuration schemas."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()

def _strategies() -> list[dict]:
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import list_strategies

    return list_strategies()


@router.get("/strategies")
def get_strategies():
    return _strategies()


@router.get("/strategies/{name}")
def get_strategy_schema(name: str):
    for s in _strategies():
        if s["name"] == name:
            return s
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")


@router.get("/strategies/{name}/source")
def strategy_source_is_not_exposed(name: str):
    if not any(s["name"] == name for s in _strategies()):
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    raise HTTPException(
        status_code=404,
        detail="Strategy source editing is not available; configure the published schema",
    )


@router.get("/exchanges")
def get_exchanges():
    """Return list of supported exchanges."""
    return [
        {
            "id": "bitget",
            "name": "Bitget",
            "default_symbol": "BTC/USDT:USDT",
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
        },
        {
            "id": "binance",
            "name": "Binance",
            "default_symbol": "BTC/USDT:USDT",
            "maker_fee": 0.0002,
            "taker_fee": 0.0004,
        },
        {
            "id": "okx",
            "name": "OKX",
            "default_symbol": "BTC/USDT:USDT",
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
        },
        {
            "id": "bybit",
            "name": "Bybit",
            "default_symbol": "BTC/USDT:USDT",
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
        },
        {
            "id": "hyperliquid",
            "name": "Hyperliquid",
            "default_symbol": "BTC/USDT:USDT",
            "maker_fee": 0.0002,
            "taker_fee": 0.0005,
        },
        {
            "id": "schwab",
            "name": "Charles Schwab",
            "default_symbol": "AAPL",
            "maker_fee": 0.0,
            "taker_fee": 0.0,
            "supports_backtest": True,
        },
    ]
