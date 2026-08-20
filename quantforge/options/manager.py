from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from quantforge.options.actions import (
    CLOSE_AND_HOLD,
    CLOSE_SHORT_CALL,
    NO_ACTION,
    OPEN_COVERED_CALL,
    ROLL_COVERED_CALL,
)


@dataclass(frozen=True, slots=True)
class OptionCandidate:
    symbol: str
    strike: float
    expiration: date
    bid: float
    ask: float
    delta: float
    open_interest: int
    volume: int

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid if mid > 0 else float("inf")


@dataclass(frozen=True, slots=True)
class ShortCallPosition:
    symbol: str
    strike: float
    expiration: date
    contracts: int
    entry_credit: float
    ask: float
    delta: float


@dataclass(frozen=True, slots=True)
class OptionManagerInput:
    ticker: str
    as_of: date
    shares: int
    minimum_core_shares: int
    maximum_covered_ratio: float
    stock_price: float
    trend_state: str
    earnings_date: date | None = None
    earnings_confirmed: bool = False
    candidates: tuple[OptionCandidate, ...] = ()
    short_calls: tuple[ShortCallPosition, ...] = ()


@dataclass(frozen=True, slots=True)
class OptionDecision:
    action: str
    reasons: tuple[str, ...]
    contract_symbol: str | None = None
    contracts: int = 0
    limit_price: float | None = None
    # ROLL_COVERED_CALL only: the replacement contract to open atomically
    # with the close. Non-roll actions leave both None.
    roll_to_symbol: str | None = None
    roll_to_price: float | None = None


class OptionManager:
    """Deterministic covered-call manager implementing the approved hard rules."""

    def __init__(
        self,
        *,
        dte_min: int = 21,
        dte_max: int = 45,
        delta_min: float = 0.15,
        delta_max: float = 0.25,
        earnings_buffer_days: int = 7,
        profit_take: float = 0.70,
        roll_delta: float = 0.50,
        maximum_covered_ratio: float = 1.0,
    ) -> None:
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.delta_min = delta_min
        self.delta_max = delta_max
        self.earnings_buffer_days = earnings_buffer_days
        self.profit_take = profit_take
        self.roll_delta = roll_delta
        self.maximum_covered_ratio = maximum_covered_ratio

    def _viable_candidates(
        self, data: OptionManagerInput
    ) -> tuple[list[OptionCandidate], bool]:
        """Candidates that pass the earnings / DTE / delta / liquidity filter.
        Returns (viable, earnings_blocked). Shared by the open and roll
        decision paths so both choose from the SAME eligible set."""
        viable = []
        earnings_blocked = False
        # An unconfirmed earnings date is speculative (calendar estimate, not
        # a company-confirmed announcement) and can drift by days. Require a
        # WIDER buffer around it so a candidate that looks "safe" against the
        # guessed date is not treated as such. Confirmed dates keep the normal
        # buffer. This makes earnings_confirmed actually drive the decision.
        earnings_buffer = (
            self.earnings_buffer_days
            if data.earnings_confirmed
            else self.earnings_buffer_days * 2
        )
        for candidate in data.candidates:
            dte = (candidate.expiration - data.as_of).days
            crosses_earnings = (
                data.earnings_date is not None
                and candidate.expiration
                >= data.earnings_date
            )
            near_earnings = (
                data.earnings_date is not None
                and (data.earnings_date - data.as_of).days
                <= dte + earnings_buffer
            )
            if crosses_earnings or near_earnings:
                earnings_blocked = True
                continue
            if not self.dte_min <= dte <= self.dte_max:
                continue
            if not self.delta_min <= candidate.delta <= self.delta_max:
                continue
            if candidate.spread_pct > 0.15:
                continue
            if candidate.open_interest <= 0 and candidate.volume <= 0:
                continue
            viable.append(candidate)
        return viable, earnings_blocked

    def _best_candidate(
        self, viable: list[OptionCandidate], stock_price: float
    ) -> OptionCandidate:
        return max(
            viable,
            key=lambda item: (
                -abs(item.delta - (self.delta_min + self.delta_max) / 2),
                -item.spread_pct,
                item.bid / stock_price,
            ),
        )

    def evaluate(self, data: OptionManagerInput) -> OptionDecision:
        for call in data.short_calls:
            profit = (
                (call.entry_credit - call.ask) / call.entry_credit
                if call.entry_credit > 0
                else 0
            )
            if profit >= self.profit_take:
                return OptionDecision(
                    CLOSE_SHORT_CALL,
                    ("已获取至少 70% 权利金，优先消除剩余 Gamma 风险",),
                    call.symbol,
                    call.contracts,
                    call.ask,
                )
            dte = (call.expiration - data.as_of).days
            trigger = max(0.4, self.roll_delta - 0.08 * max(0, (14 - dte) / 14))
            if call.delta >= trigger:
                viable, earnings_blocked = self._viable_candidates(data)
                if viable:
                    roll_to = self._best_candidate(viable, data.stock_price)
                    return OptionDecision(
                        ROLL_COVERED_CALL,
                        (
                            "短 Call Delta 达到动态管理阈值，平旧开新：滚动至更浅 "
                            "OTM/更远到期，避免先平后开留下的无对冲窗口",
                        ),
                        call.symbol,
                        call.contracts,
                        call.ask,
                        roll_to.symbol,
                        roll_to.bid,
                    )
                reason = (
                    "短 Call Delta 达到动态管理阈值，但合格候选跨越/临近财报，"
                    "先平仓避免裸对冲窗口"
                    if earnings_blocked
                    else "短 Call Delta 达到动态管理阈值，但无合格滚动候选，先平仓"
                )
                return OptionDecision(
                    CLOSE_AND_HOLD,
                    (reason,),
                    call.symbol,
                    call.contracts,
                    call.ask,
                )

        max_by_core = max(0, (data.shares - data.minimum_core_shares) // 100)
        covered_ratio = min(data.maximum_covered_ratio, self.maximum_covered_ratio)
        max_by_ratio = max(0, int(data.shares * covered_ratio) // 100)
        contracts = min(max_by_core, max_by_ratio)
        if contracts < 1:
            return OptionDecision(NO_ACTION, ("可覆盖股数不足，保留核心仓位",))
        if data.trend_state == "强势上涨":
            return OptionDecision(NO_ACTION, ("股票处于强势上涨，不主动封顶收益",))
        if data.earnings_date is None:
            return OptionDecision(
                NO_ACTION,
                ("缺少财报日期，无法确认候选合约不跨财报",),
            )

        viable, earnings_blocked = self._viable_candidates(data)
        if not viable:
            reason = (
                "候选合约跨越或过于接近财报，默认不新增 Covered Call"
                if earnings_blocked
                else "没有同时满足 DTE、Delta 与流动性要求的合约"
            )
            return OptionDecision(NO_ACTION, (reason,))
        best = self._best_candidate(viable, data.stock_price)
        return OptionDecision(
            OPEN_COVERED_CALL,
            ("不跨财报，Delta 与流动性符合规则",),
            best.symbol,
            contracts,
            best.bid,
        )
