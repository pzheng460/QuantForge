"""Pine Script utility endpoints — parse and transpile only.

Backtest and optimization are handled by the unified /backtest/ and /optimize/ endpoints.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/pine", tags=["pine"])


# --- Request / Response models ---


class PineParseRequest(BaseModel):
    pine_source: str


class PineParseResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    statement_count: int = 0
    has_strategy: bool = False


class PineTranspileRequest(BaseModel):
    pine_source: str


class PineTranspileResponse(BaseModel):
    success: bool
    python_code: str = ""
    error: Optional[str] = None


# --- Endpoints ---


@router.post("/parse", response_model=PineParseResponse)
async def parse_pine(req: PineParseRequest) -> PineParseResponse:
    """Parse Pine Script source and validate syntax."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.parser.ast_nodes import StrategyDecl

        ast = parse(req.pine_source)
        has_strategy = any(isinstance(d, StrategyDecl) for d in ast.declarations)
        return PineParseResponse(
            valid=True,
            statement_count=len(ast.body),
            has_strategy=has_strategy,
        )
    except Exception as e:
        return PineParseResponse(valid=False, error=str(e))


@router.post("/transpile", response_model=PineTranspileResponse)
async def transpile_pine(req: PineTranspileRequest) -> PineTranspileResponse:
    """Transpile Pine Script to QuantForge Python code."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.transpiler.codegen import transpile

        ast = parse(req.pine_source)
        python_code = transpile(ast)
        return PineTranspileResponse(success=True, python_code=python_code)
    except Exception as e:
        return PineTranspileResponse(success=False, error=str(e))
