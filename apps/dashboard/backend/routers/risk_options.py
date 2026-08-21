from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from quantforge.options import (
    OptionCandidate,
    OptionManagerInput,
    OptionReportStore,
    OptionsEventEngine,
    ShortCallPosition,
    candidates_from_schwab_chain,
    intent_from_option_decision,
    validate_report_ticker,
)
from apps.dashboard.backend.http_errors import safe_exception_detail
from apps.dashboard.backend.models import MAX_ORDER_NOTIONAL_USD
from quantforge.domain.instruments import AssetClass, EquityOption, InstrumentId
from quantforge.brokers.reconciliation import reconcile_schwab_account
from quantforge.risk import GlobalRiskControl
from quantforge.strategies.tsla_nvda_options import (
    TslaNvdaOptionsConfig,
    TslaNvdaOptionsManager,
)
from quantforge.adapters.schwab import SchwabExecutionAdapter
from quantforge.execution import ExecutionService, PaperExecutionAdapter
from quantforge.options.actions import EXECUTABLE_ACTIONS, ROLL_COVERED_CALL
from quantforge.options.manager import OptionDecision
from quantforge.risk import RiskEngine, RiskLimits

router = APIRouter(tags=["risk", "options"])
_GLOBAL_RISK = GlobalRiskControl()

# Server-side hard ceiling for a single real-money order placed through
# /options/schwab/run-once. The client may pick any size below this cap but
# can never raise it: encoding the ceiling in the schema's Field(le=...) makes
# over-cap requests fail with 422 before any order is submitted. Shared with
# the live-engine start path so the two real-money entry points use ONE bound.
_RUN_ONCE_MAX_NOTIONAL_USD = MAX_ORDER_NOTIONAL_USD

# Ticker charset is defined once in quantforge/options/engine.py
# (validate_report_ticker) — OptionReportStore keys reports as
# ``<root>/<ticker>/<stamp>.json``, so request-time coercion here and the
# store's own guard share one rule.
def _ticker_input(value: object) -> str:
    """Coerce a ticker to upper-case and reject anything that could escape
    the report-store path (defense in depth; the store re-checks too)."""
    if not isinstance(value, str):
        raise ValueError("ticker must be a string")
    return validate_report_ticker(value)


def _report_name(report_path: Path | str) -> str:
    """Return only the report's file name, never the absolute disk path.

    OptionReportStore.save returns an absolute Path under the operator's home
    directory; echoing that to the client leaks the server filesystem layout.
    The frontend only displays this as an opaque audit label, so the basename
    (e.g. ``20260725T100000000000Z.json``) is sufficient and safe.
    """
    return Path(report_path).name


class GlobalRiskUpdate(BaseModel):
    halted: bool
    reason: str = ""


class CandidateIn(BaseModel):
    symbol: str
    strike: float
    expiration: date
    bid: float = Field(ge=0)
    ask: float = Field(ge=0)
    delta: float
    open_interest: int = 0
    volume: int = 0


class ShortCallIn(BaseModel):
    symbol: str
    strike: float
    expiration: date
    contracts: int = Field(gt=0)
    entry_credit: float = Field(gt=0)
    ask: float = Field(ge=0)
    delta: float


class OptionAnalysisRequest(BaseModel):
    ticker: str
    as_of: date
    shares: int = Field(ge=0)
    minimum_core_shares: int = Field(ge=0)
    maximum_covered_ratio: float = Field(ge=0, le=1)
    stock_price: float = Field(gt=0)
    trend_state: str
    earnings_date: date | None = None
    earnings_confirmed: bool = False
    candidates: list[CandidateIn] = Field(default_factory=list)
    short_calls: list[ShortCallIn] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)

    _ticker_validator = field_validator("ticker", mode="before")(_ticker_input)


class SchwabOptionAnalysisRequest(BaseModel):
    ticker: str
    as_of: date
    minimum_core_shares: int = Field(ge=0)
    maximum_covered_ratio: float = Field(ge=0, le=1)
    trend_state: str
    earnings_date: date | None = None
    earnings_confirmed: bool = False
    config: dict = Field(default_factory=dict)

    _ticker_validator = field_validator("ticker", mode="before")(_ticker_input)


class SchwabOptionRunRequest(SchwabOptionAnalysisRequest):
    demo: bool = True
    max_order_notional: float = Field(
        default=10_000, gt=0, le=_RUN_ONCE_MAX_NOTIONAL_USD
    )


@router.get("/risk/global")
def get_global_risk():
    return _GLOBAL_RISK.get()


@router.put("/risk/global")
async def update_global_risk(request: GlobalRiskUpdate):
    if request.halted and _GLOBAL_RISK.path == GlobalRiskControl().path:
        # The router's control is the standard shared file: halting also
        # emergency-stops every running engine and persists the master halt
        # there. A custom-path control (isolated deployments/tests) must NOT
        # hijack the shared file — it only updates its own state below.
        from apps.dashboard.backend.live_engines import emergency_halt_all

        return await emergency_halt_all(request.reason)
    return _GLOBAL_RISK.update(halted=request.halted, reason=request.reason)


@router.post("/options/analyze")
def analyze_options(request: OptionAnalysisRequest):
    config = TslaNvdaOptionsConfig(**request.config)
    strategy = TslaNvdaOptionsManager(config)
    event = OptionManagerInput(
        ticker=request.ticker.upper(),
        as_of=request.as_of,
        shares=request.shares,
        minimum_core_shares=request.minimum_core_shares,
        maximum_covered_ratio=request.maximum_covered_ratio,
        stock_price=request.stock_price,
        trend_state=request.trend_state,
        earnings_date=request.earnings_date,
        earnings_confirmed=request.earnings_confirmed,
        candidates=tuple(
            OptionCandidate(**candidate.model_dump())
            for candidate in request.candidates
        ),
        short_calls=tuple(
            ShortCallPosition(**position.model_dump())
            for position in request.short_calls
        ),
    )
    return OptionsEventEngine(strategy).analyze(event)


def _schwab_analysis(request: SchwabOptionAnalysisRequest):
    from apps.dashboard.backend.routers.brokers import _connector
    from quantforge.brokers.schwab import SchwabAuthError

    try:
        connector = _connector()
        ticker = request.ticker.upper()
        ledger = reconcile_schwab_account(connector.get_account_snapshot())
        # Capture when the chain was actually fetched: quote-age for the risk
        # checks is measured against this timestamp, never against a synthesized
        # "now" that would make require_fresh_quote vacuous.
        fetched_at = datetime.now(UTC)
        full_chain = connector.get_option_chain(
            ticker, contract_type="CALL", strike_count=100
        )
        spot = connector.get_quote_price(ticker)
    except SchwabAuthError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_exception_detail(
                exc, prefix="Schwab authorization service unavailable"
            ),
        ) from exc
    except (OSError, ValueError) as exc:
        # Network/parse failures on the live account or chain snapshot: the
        # operator sees a stable category, the full message stays in logs.
        raise HTTPException(
            status_code=502,
            detail=safe_exception_detail(
                exc, prefix="Schwab account/chain snapshot failed"
            ),
        ) from exc
    candidates = candidates_from_schwab_chain(full_chain)
    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    short_calls = []
    for position in ledger.positions.values():
        instrument = position.instrument
        if (
            not isinstance(instrument, EquityOption)
            or instrument.underlying is None
            or instrument.underlying.symbol != ticker
            or position.quantity >= 0
        ):
            continue
        quote = by_symbol.get(instrument.id.symbol)
        if quote is None:
            continue
        short_calls.append(
            ShortCallPosition(
                symbol=instrument.id.symbol,
                strike=instrument.strike,
                expiration=instrument.expiration,
                contracts=int(abs(position.quantity)),
                entry_credit=position.average_price,
                ask=quote.ask,
                delta=quote.delta,
            )
        )
    event = OptionManagerInput(
        ticker=ticker,
        as_of=request.as_of,
        shares=int(
            ledger.quantity(
                InstrumentId(ticker, AssetClass.EQUITY, "schwab")
            )
        ),
        minimum_core_shares=request.minimum_core_shares,
        maximum_covered_ratio=request.maximum_covered_ratio,
        stock_price=spot,
        trend_state=request.trend_state,
        earnings_date=request.earnings_date,
        earnings_confirmed=request.earnings_confirmed,
        candidates=candidates,
        short_calls=tuple(short_calls),
    )
    strategy = TslaNvdaOptionsManager(
        TslaNvdaOptionsConfig(**request.config)
    )
    report = OptionsEventEngine(strategy, ledger).analyze(event)
    # Analysis lives in the research layer; execution stays here in dashboard/risk.
    from apps.research.options_research import chain_metrics_from_raw

    research = chain_metrics_from_raw(ticker, full_chain, spot)
    research["snapshot_at"] = fetched_at.isoformat()
    return report, candidates, ledger, connector, fetched_at, research


@router.post("/options/schwab/analyze")
def analyze_schwab_options(request: SchwabOptionAnalysisRequest):
    report, _candidates, _ledger, _connector, _fetched_at, research = _schwab_analysis(request)
    report_path = OptionReportStore().save(report)
    return {"report": report, "report_path": _report_name(report_path), "research": research}


@router.post("/options/schwab/run-once")
def run_schwab_options_once(request: SchwabOptionRunRequest):
    """Analyze once and automatically submit an eligible action through risk."""
    report, candidates, ledger, connector, chain_fetched_at, _research = _schwab_analysis(request)
    if report.action not in EXECUTABLE_ACTIONS:
        report_path = OptionReportStore().save(report)
        return {
            "report": report,
            "report_path": _report_name(report_path),
            "receipt": None,
        }
    candidate = next(
        (
            item
            for item in candidates
            if item.symbol == report.contract_symbol
        ),
        None,
    )
    if candidate is None:
        # The report and the chain diverged (chain refreshed between
        # analysis and submission). A raw StopIteration would surface as a
        # 500; fail explicitly instead — and BEFORE persisting the report so
        # a diverged chain does not leave an orphan report behind.
        raise HTTPException(
            status_code=409,
            detail=(
                "option chain no longer contains the analyzed contract "
                f"{report.contract_symbol}; refresh and re-analyze"
            ),
        )
    report_path = OptionReportStore().save(report)
    decision = OptionDecision(
        action=report.action,
        reasons=report.reasons,
        contract_symbol=report.contract_symbol,
        contracts=report.contracts,
        limit_price=report.limit_price,
        roll_to_symbol=report.roll_to_symbol,
        roll_to_price=report.roll_to_price,
    )
    roll_candidate = None
    if report.action == ROLL_COVERED_CALL:
        # A roll needs BOTH legs in the chain: the contract being closed and
        # the replacement being opened. Missing either means the chain
        # refreshed between analysis and submission.
        if report.roll_to_symbol is None:
            raise HTTPException(
                status_code=409,
                detail="roll decision is missing its replacement contract",
            )
        roll_candidate = next(
            (
                item
                for item in candidates
                if item.symbol == report.roll_to_symbol
            ),
            None,
        )
        if roll_candidate is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "option chain no longer contains the roll target "
                    f"{report.roll_to_symbol}; refresh and re-analyze"
                ),
            )
    intent = intent_from_option_decision(
        decision,
        candidate=candidate,
        ticker=report.ticker,
        strategy_id=report.strategy,
        # Quote age is measured against the real chain fetch time. A stale
        # chain therefore trips require_fresh_quote instead of passing.
        quote_time=chain_fetched_at,
        roll_to_candidate=roll_candidate,
    )
    # The risk wrapper shares the persistent, process-wide daily new-position
    # counter with the live engines: run-once orders draw from the same
    # budget as engine orders, and the client cannot reset it by re-issuing
    # requests (a fresh RiskEngine per request would otherwise zero it).
    from apps.dashboard.backend.live_engines import _daily_entries

    service = ExecutionService(
        risk=RiskEngine(
            RiskLimits(
                live_enabled=True,
                require_fresh_quote=True,
                max_order_notional=request.max_order_notional,
                max_daily_new_positions=10,
            ),
            global_control=_GLOBAL_RISK,
            daily_entries=_daily_entries,
        ),
        ledger=ledger,
        adapter=(
            PaperExecutionAdapter()
            if request.demo
            else SchwabExecutionAdapter(connector)
        ),
    )
    return {
        "report": report,
        "report_path": _report_name(report_path),
        "receipt": service.execute(intent),
    }
