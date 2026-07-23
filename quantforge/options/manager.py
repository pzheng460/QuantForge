from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
    ) -> None:
        self.dte_min = dte_min
        self.dte_max = dte_max
        self.delta_min = delta_min
        self.delta_max = delta_max
        self.earnings_buffer_days = earnings_buffer_days
        self.profit_take = profit_take

    def evaluate(self, data: OptionManagerInput) -> OptionDecision:
        for call in data.short_calls:
            profit = (
                (call.entry_credit - call.ask) / call.entry_credit
                if call.entry_credit > 0
                else 0
            )
            if profit >= self.profit_take:
                return OptionDecision(
                    "平仓短 Call",
                    ("已获取至少 70% 权利金，优先消除剩余 Gamma 风险",),
                    call.symbol,
                    call.contracts,
                    call.ask,
                )
            dte = (call.expiration - data.as_of).days
            trigger = max(0.4, 0.5 - 0.08 * max(0, (14 - dte) / 14))
            if call.delta >= trigger:
                return OptionDecision(
                    "买回后暂不重开",
                    ("短 Call Delta 达到动态管理阈值，需要先比较 Roll 条件",),
                    call.symbol,
                    call.contracts,
                    call.ask,
                )

        max_by_core = max(0, (data.shares - data.minimum_core_shares) // 100)
        max_by_ratio = max(0, int(data.shares * data.maximum_covered_ratio) // 100)
        contracts = min(max_by_core, max_by_ratio)
        if contracts < 1:
            return OptionDecision("不操作", ("可覆盖股数不足，保留核心仓位",))
        if data.trend_state == "强势上涨":
            return OptionDecision("不操作", ("股票处于强势上涨，不主动封顶收益",))

        viable = []
        earnings_blocked = False
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
                <= dte + self.earnings_buffer_days
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
        if not viable:
            reason = (
                "候选合约跨越或过于接近财报，默认不新增 Covered Call"
                if earnings_blocked
                else "没有同时满足 DTE、Delta 与流动性要求的合约"
            )
            return OptionDecision("不操作", (reason,))
        best = max(
            viable,
            key=lambda item: (
                -abs(item.delta - (self.delta_min + self.delta_max) / 2),
                -item.spread_pct,
                item.bid / data.stock_price,
            ),
        )
        return OptionDecision(
            "开 Covered Call",
            ("不跨财报，Delta 与流动性符合规则",),
            best.symbol,
            contracts,
            best.bid,
        )
