from __future__ import annotations

from quantforge.brokers.schwab import SchwabAmbiguousOrderError, SchwabConnector
from quantforge.domain.instruments import EquityOption
from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide
from quantforge.execution.service import SubmissionOutcomeUnknown


class SchwabExecutionAdapter:
    def __init__(self, connector: SchwabConnector):
        self.connector = connector

    def submit(self, intent: OrderIntent | MultiLegOrderIntent) -> str:
        try:
            if isinstance(intent, MultiLegOrderIntent):
                non_option = [
                    leg.instrument.id.symbol
                    for leg in intent.legs
                    if not isinstance(leg.instrument, EquityOption)
                ]
                if non_option:
                    raise ValueError(
                        "multi-leg option strategies accept option legs only: "
                        + ", ".join(non_option)
                    )
                legs = [
                    {
                        "symbol": leg.instrument.id.symbol,
                        "instruction": self._option_instruction(leg),
                        "quantity": int(leg.quantity),
                    }
                    for leg in intent.legs
                ]
                order = self.connector.place_option_strategy(
                    legs=legs, net_limit_price=intent.net_limit_price
                )
                return order.order_id
            if isinstance(intent.instrument, EquityOption):
                order = self.connector.place_option_order(
                    symbol=intent.instrument.id.symbol,
                    instruction=self._option_instruction(intent),
                    quantity=int(intent.quantity),
                    order_type=intent.order_type.value,
                    price=intent.limit_price,
                )
                return order.order_id
            if intent.side is OrderSide.BUY:
                instruction = "BUY_TO_COVER" if intent.reduce_only else "BUY"
            else:
                instruction = "SELL" if intent.reduce_only else "SELL_SHORT"
            order = self.connector.place_order(
                symbol=intent.instrument.id.symbol,
                instruction=instruction,
                quantity=intent.quantity,
                order_type=intent.order_type.value,
                price=intent.limit_price,
                stop_price=intent.stop_price,
            )
            return order.order_id
        except SchwabAmbiguousOrderError as exc:
            raise SubmissionOutcomeUnknown(str(exc)) from exc

    @staticmethod
    def _option_instruction(intent: OrderIntent) -> str:
        if intent.side is OrderSide.BUY:
            return "BUY_TO_CLOSE" if intent.reduce_only else "BUY_TO_OPEN"
        return "SELL_TO_CLOSE" if intent.reduce_only else "SELL_TO_OPEN"
