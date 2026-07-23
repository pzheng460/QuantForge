from __future__ import annotations

from dataclasses import dataclass, field
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
    intent_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True, slots=True)
class MultiLegOrderIntent:
    strategy_id: str
    legs: tuple[OrderIntent, ...]
    net_limit_price: float | None = None
    intent_id: str = field(default_factory=lambda: uuid4().hex)

