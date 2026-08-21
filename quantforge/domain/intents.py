from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from quantforge.domain.instruments import Instrument


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    strategy_id: str
    instrument: Instrument
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False
    quote_bid: float | None = None
    quote_ask: float | None = None
    quote_timestamp: datetime | None = None
    leverage: float = 1
    #: Order lifetime: "DAY" rests only for the current session; "GTC" rests
    #: until filled or canceled (used for illiquid names where a session may
    #: not produce a match). Schwab MARKET orders are DAY-only by exchange
    #: rule, so validation couples the two.
    duration: str = "DAY"
    #: Human-operator directed order (e.g. a manual liquidation from the CLI /
    #: GUI). ``True`` allows the quote-freshness gate to be waived when the
    #: operator explicitly chooses to submit on the best available quote;
    #: every other risk check (notional, spread, reduce_only netting, global
    #: halt, leverage) still applies. Autonomous engines must NEVER set it —
    #: the risk engine logs loudly when it is observed on a live path.
    operator_override: bool = False
    #: Position side for hedge-mode venues (e.g. Bitget UTA hedge accounts):
    #: ``"long"`` or ``"short"`` identifies which side this order opens or
    #: closes, so the adapter can set the venue's position-side field.
    #: ``None`` means the intent is one-way (single position direction).
    position_side: str | None = None
    intent_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.position_side is not None and self.position_side not in {"long", "short"}:
            raise ValueError("position_side must be 'long', 'short', or None")
        if not self.strategy_id or not str(self.strategy_id).strip():
            raise ValueError("strategy_id is required")
        if not isinstance(self.instrument, Instrument):
            raise ValueError("intent requires an instrument")
        if self.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError("intent side must be BUY or SELL")
        # NaNs pass every <=/> comparison, so every numeric guard pairs the
        # range check with an isfinite check (NaN quantities/leverage/prices
        # would otherwise sail through into risk and execution).
        if self.quantity is None or not math.isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("quantity must be a positive finite number")
        if self.leverage is None or not math.isfinite(self.leverage) or self.leverage <= 0:
            raise ValueError("leverage must be a positive finite number")
        if self.limit_price is not None and (
            not math.isfinite(self.limit_price) or self.limit_price <= 0
        ):
            raise ValueError("limit_price must be a positive finite number")
        if self.stop_price is not None and (
            not math.isfinite(self.stop_price) or self.stop_price <= 0
        ):
            raise ValueError("stop_price must be a positive finite number")
        if self.quote_bid is not None and (
            not math.isfinite(self.quote_bid) or self.quote_bid <= 0
        ):
            raise ValueError("quote_bid must be a positive finite number")
        if self.quote_ask is not None and (
            not math.isfinite(self.quote_ask) or self.quote_ask <= 0
        ):
            raise ValueError("quote_ask must be a positive finite number")
        if (
            self.quote_bid is not None
            and self.quote_ask is not None
            and (self.quote_ask < self.quote_bid)
        ):
            raise ValueError("quote_ask must be >= quote_bid")
        if self.quote_timestamp is not None and self.quote_timestamp.tzinfo is None:
            raise ValueError("quote_timestamp must be timezone-aware")
        if self.duration not in {"DAY", "GTC"}:
            raise ValueError("duration must be DAY or GTC")
        if self.duration == "GTC" and self.order_type is OrderType.MARKET:
            raise ValueError("GTC orders must not be MARKET (use LIMIT/STOP)")
        if not self.intent_id:
            raise ValueError("intent_id must not be empty")


@dataclass(frozen=True, slots=True)
class MultiLegOrderIntent:
    strategy_id: str
    legs: tuple[OrderIntent, ...]
    net_limit_price: float | None = None
    intent_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not self.legs:
            raise ValueError("multi-leg intent requires at least one leg")
        if not all(isinstance(leg, OrderIntent) for leg in self.legs):
            raise ValueError("every leg must be an OrderIntent")
        if any(leg.strategy_id != self.strategy_id for leg in self.legs):
            raise ValueError("all legs must share the multi-leg strategy_id")
        if not self.intent_id:
            raise ValueError("intent_id must not be empty")
        if self.net_limit_price is not None and (
            not math.isfinite(self.net_limit_price) or self.net_limit_price == 0
        ):
            raise ValueError("net_limit_price must be non-zero finite or None")
