"""Strategy listing and schema endpoints.

All strategies are Pine Script files in quantforge/pine/strategies/.
These endpoints return Pine strategy metadata with extracted input parameters.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from web.backend.models import SchemaField, StrategySchema

router = APIRouter()

_PINE_STRATEGIES_DIR = (
    Path(__file__).resolve().parents[3] / "quantforge" / "pine" / "strategies"
)


def _parse_pine_schema(pine_path: Path) -> StrategySchema:
    """Parse a .pine file and extract strategy metadata + input parameters."""
    name = pine_path.stem
    title = name.replace("_", " ").title()
    text = pine_path.read_text()

    # Extract title from strategy() declaration
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("strategy("):
            try:
                start = stripped.index('"') + 1
                end = stripped.index('"', start)
                title = stripped[start:end]
            except ValueError:
                pass
            break

    # Extract input parameters using the Pine parser
    config_fields: list[SchemaField] = []
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.optimize import extract_pine_inputs

        ast = parse(text)
        inputs = extract_pine_inputs(ast)
        for inp in inputs:
            config_fields.append(
                SchemaField(
                    name=inp.var_name,
                    type="int" if inp.input_type == "int" else "float",
                    default=int(inp.defval) if inp.input_type == "int" else inp.defval,
                    label=inp.title,
                    min=inp.minval,
                    max=inp.maxval,
                    step=inp.step,
                )
            )
    except Exception:
        pass

    return StrategySchema(
        name=name,
        display_name=title,
        default_interval="1h",
        config_fields=config_fields,
        filter_fields=[],
    )


def _list_pine_strategies() -> list[StrategySchema]:
    """Scan .pine files and extract strategy metadata."""
    results = []
    if not _PINE_STRATEGIES_DIR.exists():
        return results
    for f in sorted(_PINE_STRATEGIES_DIR.glob("*.pine")):
        try:
            results.append(_parse_pine_schema(f))
        except Exception:
            # Fallback: minimal schema
            results.append(
                StrategySchema(
                    name=f.stem,
                    display_name=f.stem.replace("_", " ").title(),
                    default_interval="1h",
                    config_fields=[],
                    filter_fields=[],
                )
            )
    return results


@router.get("/strategies")
def get_strategies():
    """Return all Pine Script strategies."""
    return _list_pine_strategies()


@router.get("/strategies/{name}")
def get_strategy_schema(name: str):
    """Return metadata for a single Pine strategy."""
    for s in _list_pine_strategies():
        if s.name == name:
            return s
    raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")


@router.get("/strategies/{name}/source")
def get_strategy_source(name: str):
    """Return the Pine Script source code for a strategy."""
    pine_file = _PINE_STRATEGIES_DIR / f"{name}.pine"
    if not pine_file.exists():
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    return {"source": pine_file.read_text()}


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
    ]
