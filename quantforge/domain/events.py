from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from quantforge.domain.instruments import InstrumentId


class EventType(StrEnum):
    BAR = "bar"
    QUOTE = "quote"
    OPTION_CHAIN = "option_chain"
    FUNDING_RATE = "funding_rate"
    ORDER = "order"
    FILL = "fill"
    EXPIRATION = "expiration"
    ASSIGNMENT = "assignment"
    CORPORATE_ACTION = "corporate_action"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    type: EventType
    timestamp: datetime
    instrument: InstrumentId | None = None
    payload: Any = None


@dataclass(frozen=True, slots=True)
class Assignment:
    option: InstrumentId
    underlying: InstrumentId
    option_quantity: float
    share_quantity: float
    strike: float
    reason: str
