from __future__ import annotations

from datetime import datetime

from quantforge.domain.instruments import (
    AssetClass,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import (
    MultiLegOrderIntent,
    OrderIntent,
    OrderSide,
    OrderType,
)
from quantforge.options.actions import (
    EXECUTABLE_ACTIONS,
    OPEN_COVERED_CALL,
    ROLL_COVERED_CALL,
)
from quantforge.options.manager import OptionCandidate, OptionDecision


def _option_from_candidate(
    candidate: OptionCandidate, ticker: str
) -> EquityOption:
    return EquityOption(
        id=InstrumentId(
            candidate.symbol, AssetClass.EQUITY_OPTION, "schwab"
        ),
        underlying=InstrumentId(ticker, AssetClass.EQUITY, "schwab"),
        expiration=candidate.expiration,
        strike=candidate.strike,
        right=OptionRight.CALL,
    )


def intent_from_option_decision(
    decision: OptionDecision,
    *,
    candidate: OptionCandidate,
    ticker: str,
    strategy_id: str,
    quote_time: datetime,
    roll_to_candidate: OptionCandidate | None = None,
) -> OrderIntent | MultiLegOrderIntent:
    if decision.action not in EXECUTABLE_ACTIONS:
        raise ValueError(f"decision is not directly executable: {decision.action}")
    if decision.action == ROLL_COVERED_CALL:
        if roll_to_candidate is None:
            raise ValueError(
                "ROLL_COVERED_CALL decision requires roll_to_candidate"
            )
        if decision.roll_to_symbol != roll_to_candidate.symbol:
            raise ValueError(
                "roll_to_candidate symbol mismatch: "
                f"decision={decision.roll_to_symbol} "
                f"candidate={roll_to_candidate.symbol}"
            )
        close = OrderIntent(
            strategy_id=strategy_id,
            instrument=_option_from_candidate(candidate, ticker),
            side=OrderSide.BUY,
            quantity=decision.contracts,
            order_type=OrderType.LIMIT,
            limit_price=decision.limit_price,
            reduce_only=True,
            quote_bid=candidate.bid,
            quote_ask=candidate.ask,
            quote_timestamp=quote_time,
        )
        reopen = OrderIntent(
            strategy_id=strategy_id,
            instrument=_option_from_candidate(roll_to_candidate, ticker),
            side=OrderSide.SELL,
            quantity=decision.contracts,
            order_type=OrderType.LIMIT,
            limit_price=decision.roll_to_price,
            reduce_only=False,
            quote_bid=roll_to_candidate.bid,
            quote_ask=roll_to_candidate.ask,
            quote_timestamp=quote_time,
        )
        # Net credit of the roll; the Schwab adapter submits both legs as ONE
        # option strategy order so there is no unhedged window between the
        # close and the reopen.
        return MultiLegOrderIntent(
            strategy_id=strategy_id,
            legs=(close, reopen),
            net_limit_price=(decision.roll_to_price or 0.0)
            - (decision.limit_price or 0.0),
        )
    option = _option_from_candidate(candidate, ticker)
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
