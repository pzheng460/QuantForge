from __future__ import annotations

from datetime import datetime

from quantforge.domain.instruments import (
    AssetClass,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import OrderIntent, OrderSide, OrderType
from quantforge.options.actions import EXECUTABLE_ACTIONS, OPEN_COVERED_CALL
from quantforge.options.manager import OptionCandidate, OptionDecision


def intent_from_option_decision(
    decision: OptionDecision,
    *,
    candidate: OptionCandidate,
    ticker: str,
    strategy_id: str,
    quote_time: datetime,
) -> OrderIntent:
    if decision.action not in EXECUTABLE_ACTIONS:
        raise ValueError(f"decision is not directly executable: {decision.action}")
    underlying = InstrumentId(ticker, AssetClass.EQUITY, "schwab")
    option = EquityOption(
        id=InstrumentId(
            candidate.symbol, AssetClass.EQUITY_OPTION, "schwab"
        ),
        underlying=underlying,
        expiration=candidate.expiration,
        strike=candidate.strike,
        right=OptionRight.CALL,
    )
    closing = decision.action != OPEN_COVERED_CALL
    return OrderIntent(
        strategy_id=strategy_id,
        instrument=option,
        side=OrderSide.BUY if closing else OrderSide.SELL,
        quantity=decision.contracts,
        order_type=OrderType.LIMIT,
        limit_price=candidate.ask if closing else candidate.bid,
        reduce_only=closing,
        quote_bid=candidate.bid,
        quote_ask=candidate.ask,
        quote_timestamp=quote_time,
    )
