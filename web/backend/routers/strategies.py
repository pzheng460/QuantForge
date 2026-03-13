"""Strategy listing and schema endpoints.

NOTE: The old Python strategy registry (strategy/) has been removed.
All strategies are now Pine Script files in quantforge/pine/strategies/.
These endpoints return Pine strategy metadata instead.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_PINE_STRATEGIES_DIR = (
    Path(__file__).resolve().parents[3] / "quantforge" / "pine" / "strategies"
)


def _list_pine_strategies() -> list[dict]:
    """Scan .pine files and extract strategy metadata."""
    results = []
    if not _PINE_STRATEGIES_DIR.exists():
        return results
    for f in sorted(_PINE_STRATEGIES_DIR.glob("*.pine")):
        name = f.stem
        # Extract title from strategy() declaration
        title = name.replace("_", " ").title()
        try:
            text = f.read_text()
            for line in text.splitlines():
                if line.strip().startswith("strategy("):
                    # Extract first quoted string as display name
                    start = line.index('"') + 1
                    end = line.index('"', start)
                    title = line[start:end]
                    break
        except Exception:
            pass
        results.append({"name": name, "display_name": title, "path": str(f)})
    return results


@router.get("/strategies")
def get_strategies():
    """Return all Pine Script strategies."""
    return _list_pine_strategies()


@router.get("/strategies/{name}")
def get_strategy_schema(name: str):
    """Return metadata for a single Pine strategy."""
    for s in _list_pine_strategies():
        if s["name"] == name:
            return s
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")


@router.get("/exchanges")
def get_exchanges():
    """Return list of supported exchanges."""
    return [
        {"id": "bitget", "name": "Bitget", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
        {"id": "binance", "name": "Binance", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0004},
        {"id": "okx", "name": "OKX", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
        {"id": "bybit", "name": "Bybit", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
        {"id": "hyperliquid", "name": "Hyperliquid", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
    ]
