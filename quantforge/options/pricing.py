from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from quantforge.domain.instruments import EquityOption, OptionRight


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


@dataclass(frozen=True, slots=True)
class OptionQuote:
    bid: float
    ask: float
    mark: float
    delta: float
    iv: float
    quality: str = "approximate_unvalidated"


class ApproximateOptionPricer:
    """Black-Scholes approximation for research; never presented as historical NBBO."""

    def quote(
        self,
        contract: EquityOption,
        *,
        spot: float,
        valuation_date: date,
        volatility: float,
        risk_free_rate: float = 0.04,
        spread_pct: float = 0.06,
    ) -> OptionQuote:
        # NaN defeats the <=/> comparisons below (NaN <= 0 is False), so a
        # NaN spot/volatility would slip past the positivity guard and yield a
        # quote carrying NaN delta/mark/ask — reject non-finite inputs first.
        if (
            not math.isfinite(spot)
            or not math.isfinite(volatility)
            or not math.isfinite(risk_free_rate)
            or not math.isfinite(spread_pct)
            or spot <= 0
            or volatility <= 0
            or spread_pct <= 0
        ):
            raise ValueError(
                "spot and volatility must be positive finite numbers "
                "and risk_free_rate/spread_pct finite"
            )
        years = max((contract.expiration - valuation_date).days / 365, 1 / 365)
        root_t = math.sqrt(years)
        d1 = (
            math.log(spot / contract.strike)
            + (risk_free_rate + volatility**2 / 2) * years
        ) / (volatility * root_t)
        d2 = d1 - volatility * root_t
        discount = math.exp(-risk_free_rate * years)
        if contract.right is OptionRight.CALL:
            mark = spot * _normal_cdf(d1) - contract.strike * discount * _normal_cdf(d2)
            delta = _normal_cdf(d1)
        else:
            mark = contract.strike * discount * _normal_cdf(-d2) - spot * _normal_cdf(-d1)
            delta = _normal_cdf(d1) - 1
        mark = max(mark, 0.01)
        half = max(mark * spread_pct / 2, 0.01)
        return OptionQuote(
            bid=max(0, mark - half),
            ask=mark + half,
            mark=mark,
            delta=delta,
            iv=volatility,
        )

