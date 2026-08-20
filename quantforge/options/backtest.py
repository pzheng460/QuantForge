from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from quantforge.backtest.engine import BacktestResult, BacktestTrade
from quantforge.domain.instruments import (
    AssetClass,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.options.pricing import ApproximateOptionPricer


@dataclass(frozen=True, slots=True)
class ApproximateOptionBacktest:
    result: BacktestResult
    quality: str = "approximate_unvalidated"


@dataclass(frozen=True, slots=True)
class ManagedCoveredCallConfig:
    minimum_core_shares: int = 0
    maximum_covered_ratio: float = 0.50
    dte_min: int = 21
    dte_max: int = 45
    profit_take_pct: float = 0.70
    #: Delta at which an open short call is managed (bought back / rolled),
    #: mirroring the live OptionManager's roll_delta knob.
    roll_delta: float = 0.50
    earnings_buffer_days: int = 7
    stock_fee_per_share: float = 0.005
    option_fee_per_contract: float = 0.65
    modeled_spread_pct: float = 0.06

    def __post_init__(self) -> None:
        if not 0.30 <= self.roll_delta <= 0.80:
            raise ValueError("roll_delta must be between 0.30 and 0.80")
        if not 0 <= self.maximum_covered_ratio <= 1:
            raise ValueError("maximum_covered_ratio must be between zero and one")


@dataclass(frozen=True, slots=True)
class ManagedCoveredCallBacktest:
    ticker: str
    strategy_version: str
    quality: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    buy_hold_return_pct: float
    realized_option_pnl: float
    total_costs: float
    option_trades: int
    assignments: int
    max_contracts_open: int
    action_counts: dict[str, int]
    equity_curve: tuple[float, ...]
    #: Per-option-trade ledger in the shared BacktestResult shape, so the
    #: dashboard can render individual trades with entry/exit/pnl/fee just
    #: like the strategy backtester.
    result: BacktestResult | None = None


@dataclass(slots=True)
class _ManagedShortCall:
    option: EquityOption
    contracts: int
    entry_credit: float
    entry_cost: float
    entry_bar: int = 0
    #: Favorable/adverse excursion of the short-call position in dollars.
    mfe: float = 0.0
    mae: float = 0.0


@dataclass(frozen=True, slots=True)
class _PendingAction:
    """A management decision taken at one bar's close, filled at the NEXT
    bar's open.

    This mirrors the shared backtest engine's next-bar-open fills: the
    decision inputs (close price, close quote, indicators) are never also the
    fill price, which removes same-bar lookahead from the options models.
    """

    action: str  # "open" | "close" | "roll"
    option: EquityOption | None = None
    contracts: int = 0


def _date(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc
    ).date()


def _realized_volatility(closes: list[float], window: int = 20) -> float:
    selected = closes[-(window + 1) :]
    returns = [
        math.log(selected[index] / selected[index - 1])
        for index in range(1, len(selected))
        if selected[index - 1] > 0
    ]
    return (
        max(0.10, statistics.pstdev(returns) * math.sqrt(252))
        if len(returns) > 1
        else 0.40
    )


def _trend_state(
    rows: list[list | tuple],
    closes: list[float],
    stress_history: list[float],
) -> tuple[str, bool]:
    spot = closes[-1]
    ma20 = statistics.fmean(closes[-20:])
    ma50 = statistics.fmean(closes[-50:])
    ma200 = statistics.fmean(closes[-200:])
    recent = rows[-15:]
    true_ranges = []
    for index, row in enumerate(recent):
        previous_close = float(recent[index - 1][4]) if index else float(row[1])
        true_ranges.append(
            max(
                float(row[2]) - float(row[3]),
                abs(float(row[2]) - previous_close),
                abs(float(row[3]) - previous_close),
            )
        )
    atr14 = statistics.fmean(true_ranges[-14:])
    rv20 = _realized_volatility(closes)
    return20 = spot / closes[-21] - 1 if len(closes) >= 21 else 0.0
    denominator = max(atr14, spot * 0.001)
    score = (
        0.35 * (spot - ma20) / denominator
        + 0.30 * (ma20 - ma50) / denominator
        + 0.20 * (ma50 - ma200) / denominator
        + 0.15 * return20 / max(rv20, 0.01)
    )
    stress = atr14 / spot
    high_volatility = False
    if len(stress_history) >= 20:
        ordered = sorted(stress_history)
        threshold = ordered[min(len(ordered) - 1, int(len(ordered) * 0.80))]
        high_volatility = stress >= threshold
    stress_history.append(stress)
    if score >= 1.5:
        state = "强势上涨"
    elif score >= 0.5:
        state = "温和上涨"
    elif score > -0.5:
        state = "横盘"
    elif score > -1.5:
        state = "温和下跌"
    else:
        state = "强势下跌"
    return state, high_volatility


def _target_delta(ticker: str, trend_state: str) -> float | None:
    # Parity with the live OptionManager: only a strong uptrend blocks new
    # covered calls (manager.py returns NO_ACTION on 强势上涨). A strong
    # downtrend still opens (short calls are naturally bearish), so it maps
    # to the same target as a mild downtrend instead of None.
    if trend_state == "强势下跌":
        trend_state = "温和下跌"
    targets = {
        "TSLA": {
            "强势上涨": None,
            "温和上涨": 0.14,
            "横盘": 0.19,
            "温和下跌": 0.25,
        },
        "NVDA": {
            "强势上涨": None,
            "温和上涨": 0.16,
            "横盘": 0.215,
            "温和下跌": 0.25,
        },
    }
    return targets[ticker][trend_state]


def _next_earnings(
    day: date,
    earnings_dates: tuple[date, ...],
) -> date | None:
    return next((item for item in earnings_dates if item >= day), None)


def _modeled_call(
    ticker: str,
    *,
    day: date,
    spot: float,
    volatility: float,
    dte: int,
    target_delta: float,
    minimum_strike: float = 0.0,
    spread_pct: float,
) -> tuple[EquityOption, object]:
    pricer = ApproximateOptionPricer()
    expiration = day + timedelta(days=dte)
    candidates = []
    for step in range(100, 171):
        strike = round(spot * step / 100, 2)
        if strike <= minimum_strike:
            continue
        option = EquityOption(
            id=InstrumentId(
                f"MODEL-{ticker}-{expiration}-{strike}-C",
                AssetClass.EQUITY_OPTION,
                "model",
            ),
            expiration=expiration,
            strike=strike,
            right=OptionRight.CALL,
        )
        quote = pricer.quote(
            option,
            spot=spot,
            valuation_date=day,
            volatility=volatility,
            spread_pct=spread_pct,
        )
        candidates.append((abs(quote.delta - target_delta), option, quote))
    if not candidates:
        raise ValueError("no modeled call candidate")
    _, option, quote = min(candidates, key=lambda item: item[0])
    return option, quote


def run_managed_covered_call_approximation(
    ticker: str,
    bars: list[list | tuple],
    *,
    initial_capital: float,
    config: ManagedCoveredCallConfig | None = None,
    earnings_dates: tuple[date, ...] = (),
    evaluation_start: date | None = None,
) -> ManagedCoveredCallBacktest:
    """Stateful managed covered-call approximation using modeled option quotes.

    Management decisions (open / close / roll) are made at each bar's close
    and filled at the NEXT bar's open, mirroring the shared backtest engine's
    next-bar-open semantics so decision signals are never also the fill price.
    Same-bar settlements (expiry/assignment) are events priced at the close,
    not fills the strategy can schedule.
    """
    ticker = ticker.upper()
    if ticker not in {"TSLA", "NVDA"}:
        raise ValueError("ticker must be TSLA or NVDA")
    if not bars or initial_capital <= 0:
        raise ValueError("bars and positive initial capital are required")
    cfg = config or ManagedCoveredCallConfig()
    if not 0 <= cfg.maximum_covered_ratio <= 1:
        raise ValueError("maximum_covered_ratio must be between zero and one")
    ordered = sorted(bars, key=lambda row: int(row[0]))
    start_index = next(
        (
            index
            for index, row in enumerate(ordered)
            if evaluation_start is None or _date(int(row[0])) >= evaluation_start
        ),
        len(ordered),
    )
    if start_index >= len(ordered):
        raise ValueError("evaluation_start is outside available bars")
    first_spot = float(ordered[start_index][4])
    shares = math.floor(initial_capital / first_spot)
    stock_cost = shares * cfg.stock_fee_per_share
    cash = initial_capital - shares * first_spot - stock_cost
    total_costs = stock_cost
    calls: _ManagedShortCall | None = None
    equity_curve: list[float] = []
    closes: list[float] = []
    stress_history: list[float] = []
    actions: Counter[str] = Counter()
    realized_option_pnl = 0.0
    option_trades = 0
    assignments = 0
    max_contracts_open = 0
    trades: list[BacktestTrade] = []
    next_entry_date: date | None = None
    buy_hold_shares = shares
    buy_hold_cash = initial_capital - buy_hold_shares * first_spot
    pricer = ApproximateOptionPricer()
    pending: _PendingAction | None = None

    def fill_pending(
        action: _PendingAction,
        spot_exec: float,
        day_exec: date,
        vol_exec: float,
        fill_bar: int,
    ) -> _ManagedShortCall | None:
        """Execute a pending decision against the given price level."""
        nonlocal cash, total_costs, realized_option_pnl, option_trades
        nonlocal max_contracts_open, calls, trades
        if action.action == "close":
            assert calls is not None
            close_quote = pricer.quote(
                calls.option,
                spot=spot_exec,
                valuation_date=day_exec,
                volatility=vol_exec,
                spread_pct=cfg.modeled_spread_pct,
            )
            close_fee = cfg.option_fee_per_contract * calls.contracts
            cash -= close_quote.ask * 100 * calls.contracts + close_fee
            total_costs += close_fee
            pnl = (
                calls.entry_credit - close_quote.ask
            ) * 100 * calls.contracts - calls.entry_cost - close_fee
            realized_option_pnl += pnl
            option_trades += 1
            trades.append(
                BacktestTrade(
                    direction="short",
                    entry_bar=calls.entry_bar,
                    entry_price=calls.entry_credit,
                    exit_bar=fill_bar,
                    exit_price=close_quote.ask,
                    quantity=calls.contracts * 100,
                    pnl=pnl,
                    fee=calls.entry_cost + close_fee,
                    mfe=calls.mfe,
                    mae=calls.mae,
                )
            )
            return None
        open_quote = pricer.quote(
            action.option,
            spot=spot_exec,
            valuation_date=day_exec,
            volatility=vol_exec,
            spread_pct=cfg.modeled_spread_pct,
        )
        open_fee = cfg.option_fee_per_contract * action.contracts
        cash += open_quote.bid * 100 * action.contracts - open_fee
        total_costs += open_fee
        max_contracts_open = max(max_contracts_open, action.contracts)
        return _ManagedShortCall(
            action.option,
            action.contracts,
            open_quote.bid,
            open_fee,
            entry_bar=fill_bar,
        )

    for index, row in enumerate(ordered):
        day = _date(int(row[0]))
        spot_open = float(row[1])
        spot = float(row[4])

        # Fill yesterday's decision at today's open.
        if index >= start_index and pending is not None:
            calls = fill_pending(
                pending, spot_open, day, _realized_volatility(closes), fill_bar=index
            )
            pending = None

        closes.append(spot)
        if index < start_index:
            if len(closes) >= 200:
                _trend_state(ordered[: index + 1], closes, stress_history)
            continue

        volatility = _realized_volatility(closes)
        liability = 0.0
        trend, high_volatility = _trend_state(
            ordered[: index + 1], closes, stress_history
        )

        if calls is not None:
            quote = pricer.quote(
                calls.option,
                spot=spot,
                valuation_date=day,
                volatility=volatility,
                spread_pct=cfg.modeled_spread_pct,
            )
            if day >= calls.option.expiration:
                # Settlement/assignment is an event priced at the close, not a
                # fill the strategy could schedule.
                assigned = spot > calls.option.strike
                settlement = max(0.0, spot - calls.option.strike)
                settle_pnl = (
                    calls.entry_credit - settlement
                ) * 100 * calls.contracts - calls.entry_cost
                realized_option_pnl += settle_pnl
                if assigned:
                    assigned_shares = min(shares, calls.contracts * 100)
                    cash += calls.option.strike * assigned_shares
                    shares -= assigned_shares
                    assignments += 1
                    actions["assignment"] += 1
                else:
                    actions["expire"] += 1
                option_trades += 1
                trades.append(
                    BacktestTrade(
                        direction="short",
                        entry_bar=calls.entry_bar,
                        entry_price=calls.entry_credit,
                        exit_bar=index,
                        exit_price=settlement,
                        quantity=calls.contracts * 100,
                        pnl=settle_pnl,
                        fee=calls.entry_cost,
                        mfe=calls.mfe,
                        mae=calls.mae,
                    )
                )
                calls = None
            else:
                dte = (calls.option.expiration - day).days
                profit_pct = (
                    (calls.entry_credit - quote.ask) / calls.entry_credit
                    if calls.entry_credit > 0
                    else 0.0
                )
                # Delta management threshold EXACTLY as the live OptionManager
                # computes it (manager.py): the strategy's roll_delta knob,
                # tightened linearly as expiry approaches.
                delta_trigger = max(
                    0.4, cfg.roll_delta - 0.08 * max(0, (14 - dte) / 14)
                )
                if profit_pct >= cfg.profit_take_pct:
                    pending = _PendingAction("close")
                    actions["profit_take"] += 1
                elif quote.delta >= delta_trigger:
                    # Parity with the live manager's CLOSE_AND_HOLD: buy back
                    # the breached short call and hold flat; a later cycle runs
                    # the trend/earnings/coverage gates before reopening. The
                    # live manager NEVER rolls up, so neither do we.
                    pending = _PendingAction("close")
                    actions["delta_close"] += 1

        if calls is None and pending is None:
            if next_entry_date is not None and day < next_entry_date:
                actions["cooldown"] += 1
            elif len(closes) < 200:
                actions["warmup"] += 1
            else:
                target_delta = _target_delta(ticker, trend)
                max_by_core = max(0, (shares - cfg.minimum_core_shares) // 100)
                max_by_ratio = max(
                    0,
                    math.floor(shares * cfg.maximum_covered_ratio / 100),
                )
                contracts = min(max_by_core, max_by_ratio)
                if contracts < 1:
                    actions["coverage_block"] += 1
                elif target_delta is None:
                    actions[
                        "strong_uptrend_block"
                        if trend == "强势上涨"
                        else "downside_review"
                    ] += 1
                else:
                    dte = 35 if ticker == "TSLA" and high_volatility else 28
                    dte = max(cfg.dte_min, min(cfg.dte_max, dte))
                    next_earnings = _next_earnings(day, earnings_dates)
                    earnings_buffer = cfg.earnings_buffer_days
                    # Parity with the live OptionManager (manager.py): an
                    # entry is blocked when the next earnings date is UNKNOWN
                    # (no calendar / calendar exhausted) OR when a known
                    # earnings date falls within dte+buffer days.
                    if (
                        next_earnings is None
                        or (next_earnings - day).days <= dte + earnings_buffer
                    ):
                        actions["earnings_block"] += 1
                    else:
                        option, _quote = _modeled_call(
                            ticker,
                            day=day,
                            spot=spot,
                            volatility=volatility,
                            dte=dte,
                            target_delta=target_delta,
                            spread_pct=cfg.modeled_spread_pct,
                        )
                        pending = _PendingAction("open", option, contracts)
                        actions["open_covered_call"] += 1
                        next_entry_date = day + timedelta(days=1)

        if calls is not None:
            mark = pricer.quote(
                calls.option,
                spot=spot,
                valuation_date=day,
                volatility=volatility,
                spread_pct=cfg.modeled_spread_pct,
            )
            liability = mark.ask * 100 * calls.contracts
            # Track per-bar favorable/adverse excursion for the shared trade
            # ledger (dollar PnL of the short option at the modeled mark).
            held_pnl = (
                calls.entry_credit - mark.ask
            ) * 100 * calls.contracts - calls.entry_cost
            calls.mfe = max(calls.mfe, held_pnl)
            calls.mae = min(calls.mae, held_pnl)
        equity_curve.append(cash + shares * spot - liability)

    last_day = _date(int(ordered[-1][0]))
    last_spot = float(ordered[-1][4])
    if pending is not None:
        # Terminal bar: there is no next open to defer to, and no future data
        # exists, so a decision made on the final close fills at that close.
        calls = fill_pending(
            pending,
            last_spot,
            last_day,
            _realized_volatility(closes),
            fill_bar=len(ordered) - 1,
        )
        pending = None

    if calls is not None:
        quote = pricer.quote(
            calls.option,
            spot=last_spot,
            valuation_date=last_day,
            volatility=_realized_volatility(closes),
            spread_pct=cfg.modeled_spread_pct,
        )
        fee = cfg.option_fee_per_contract * calls.contracts
        cash -= quote.ask * 100 * calls.contracts + fee
        total_costs += fee
        final_pnl = (
            calls.entry_credit - quote.ask
        ) * 100 * calls.contracts - calls.entry_cost - fee
        realized_option_pnl += final_pnl
        option_trades += 1
        actions["final_close"] += 1
        trades.append(
            BacktestTrade(
                direction="short",
                entry_bar=calls.entry_bar,
                entry_price=calls.entry_credit,
                exit_bar=len(ordered) - 1,
                exit_price=quote.ask,
                quantity=calls.contracts * 100,
                pnl=final_pnl,
                fee=calls.entry_cost + fee,
                mfe=calls.mfe,
                mae=calls.mae,
            )
        )
        equity_curve[-1] = cash + shares * last_spot

    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    final_equity = equity_curve[-1]
    buy_hold_final = buy_hold_cash + buy_hold_shares * float(ordered[-1][4])
    return ManagedCoveredCallBacktest(
        ticker=ticker,
        strategy_version="managed_cc_v1_default_unvalidated",
        quality="approximate_unvalidated",
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return_pct=(final_equity / initial_capital - 1) * 100,
        max_drawdown_pct=max_drawdown * 100,
        buy_hold_return_pct=(buy_hold_final / initial_capital - 1) * 100,
        realized_option_pnl=realized_option_pnl,
        total_costs=total_costs,
        option_trades=option_trades,
        assignments=assignments,
        max_contracts_open=max_contracts_open,
        action_counts=dict(sorted(actions.items())),
        equity_curve=tuple(equity_curve),
        result=BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=initial_capital,
        ),
    )


def run_covered_call_approximation(
    bars: list[list | tuple],
    *,
    initial_capital: float,
    dte: int = 30,
    target_delta: float = 0.20,
    coverage_ratio: float = 0.50,
) -> ApproximateOptionBacktest:
    """Model a repeated covered-call overlay; prices are not historical NBBO.

    Entry decisions are made at one bar's close and filled at the NEXT bar's
    open, so the decision quote is never also the fill price.
    """
    if not bars or initial_capital <= 0:
        raise ValueError("bars and positive initial capital are required")
    first_spot = float(bars[0][4])
    shares = math.floor(initial_capital / first_spot)
    cash = initial_capital - shares * first_spot
    max_contracts = math.floor(shares * coverage_ratio / 100)
    if max_contracts < 1:
        raise ValueError("capital is insufficient for one covered-call contract")

    pricer = ApproximateOptionPricer()
    closes: list[float] = []
    equity: list[float] = []
    targets: list[int] = []
    trades: list[BacktestTrade] = []
    contract: EquityOption | None = None
    entry_index = 0
    entry_credit = 0.0
    contracts = max_contracts
    pending_entry: EquityOption | None = None

    for index, row in enumerate(bars):
        spot_open = float(row[1])
        spot = float(row[4])
        valuation_date = _date(int(row[0]))

        # Fill yesterday's entry decision at today's open.
        if pending_entry is not None:
            execution_volatility = _realized_volatility(closes)
            quote = pricer.quote(
                pending_entry,
                spot=spot_open,
                valuation_date=valuation_date,
                volatility=execution_volatility,
            )
            entry_credit = quote.bid
            entry_index = index
            cash += entry_credit * 100 * contracts
            contract = pending_entry
            pending_entry = None

        closes.append(spot)
        returns = [
            math.log(closes[i] / closes[i - 1])
            for i in range(max(1, len(closes) - 20), len(closes))
            if closes[i - 1] > 0
        ]
        volatility = (
            max(0.10, statistics.pstdev(returns) * math.sqrt(252))
            if len(returns) > 1
            else 0.40
        )

        if contract is not None and valuation_date >= contract.expiration:
            intrinsic = max(0.0, spot - contract.strike)
            pnl = (entry_credit - intrinsic) * 100 * contracts
            cash -= intrinsic * 100 * contracts
            trades.append(
                BacktestTrade(
                    direction="short",
                    entry_bar=entry_index,
                    entry_price=entry_credit,
                    exit_bar=index,
                    exit_price=intrinsic,
                    quantity=contracts * 100,
                    pnl=pnl,
                    fee=0,
                )
            )
            contract = None

        liability = 0.0
        if contract is None and pending_entry is None:
            # Decision only — the fill happens at the next bar's open.
            expiration = valuation_date + timedelta(days=dte)
            candidates = []
            for step in range(101, 151):
                strike = round(spot * step / 100, 2)
                option = EquityOption(
                    id=InstrumentId(
                        f"MODEL-{expiration}-{strike}-C",
                        AssetClass.EQUITY_OPTION,
                        "model",
                    ),
                    expiration=expiration,
                    strike=strike,
                    right=OptionRight.CALL,
                )
                quote = pricer.quote(
                    option,
                    spot=spot,
                    valuation_date=valuation_date,
                    volatility=volatility,
                )
                candidates.append((abs(quote.delta - target_delta), option, quote))
            _, pending_entry, _ = min(candidates, key=lambda item: item[0])
        elif contract is not None:
            quote = pricer.quote(
                contract,
                spot=spot,
                valuation_date=valuation_date,
                volatility=volatility,
            )
            liability = quote.ask * 100 * contracts

        equity.append(cash + shares * spot - liability)
        targets.append(1)

    if contract is not None:
        spot = float(bars[-1][4])
        intrinsic = max(0.0, spot - contract.strike)
        pnl = (entry_credit - intrinsic) * 100 * contracts
        cash -= intrinsic * 100 * contracts
        trades.append(
            BacktestTrade(
                direction="short",
                entry_bar=entry_index,
                entry_price=entry_credit,
                exit_bar=len(bars) - 1,
                exit_price=intrinsic,
                quantity=contracts * 100,
                pnl=pnl,
                fee=0,
            )
        )
        equity[-1] = cash + shares * spot

    return ApproximateOptionBacktest(
        BacktestResult(
            trades=trades,
            equity_curve=equity,
            targets=targets,
            initial_capital=initial_capital,
        )
    )
