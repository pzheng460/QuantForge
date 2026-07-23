from __future__ import annotations

import math
import statistics
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


def _date(timestamp_ms: int) -> date:
    return datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc
    ).date()


def run_covered_call_approximation(
    bars: list[list | tuple],
    *,
    initial_capital: float,
    dte: int = 30,
    target_delta: float = 0.20,
    coverage_ratio: float = 0.50,
) -> ApproximateOptionBacktest:
    """Model a repeated covered-call overlay; prices are not historical NBBO."""
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

    for index, row in enumerate(bars):
        spot = float(row[4])
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
        valuation_date = _date(int(row[0]))
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
        if contract is None:
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
            _, contract, quote = min(candidates, key=lambda item: item[0])
            entry_credit = quote.bid
            entry_index = index
            cash += entry_credit * 100 * contracts
            liability = quote.ask * 100 * contracts
        else:
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
