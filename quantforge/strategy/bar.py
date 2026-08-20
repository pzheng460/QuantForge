from __future__ import annotations

from dataclasses import dataclass, replace

from quantforge.strategy.api import Strategy
from quantforge.strategy.indicators import Indicator, create_indicator


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    index: int = 0


@dataclass(frozen=True, slots=True)
class PositionTarget:
    position: int
    stop_price: float | None = None
    trailing_distance: float | None = None
    #: Explicit sentinel to CLEAR any active stop/trailing on the position.
    #: Without it a held position could never cancel a previously-armed stop
    #: (the engine keeps active_stop until the position closes or is
    #: overwritten), forcing an unintended exit. Set True to disarm.
    clear_risk_exits: bool = False

    @property
    def has_risk_order(self) -> bool:
        return (
            self.stop_price is not None
            or self.trailing_distance is not None
            or self.clear_risk_exits
        )


class BarStrategy(Strategy):
    timeframe = "1h"

    def __init__(self, config):
        super().__init__(config)
        self.position = 0
        self.bar_index = 0
        self._indicators: list[Indicator] = []
        self.setup()

    def setup(self) -> None:
        pass

    def add_indicator(self, kind: str, *args) -> Indicator:
        indicator = create_indicator(kind, *args)
        self._indicators.append(indicator)
        return indicator

    def on_bar(self, bar: Bar) -> PositionTarget:
        return PositionTarget(self.position)

    def process_bar(self, bar: Bar) -> PositionTarget:
        # Bars are frozen/immutable; stamp the strategy's running bar index by
        # copying rather than mutating the shared feed object.
        if bar.index != self.bar_index:
            bar = replace(bar, index=self.bar_index)
        for indicator in self._indicators:
            indicator._update(bar)
        target = self.on_bar(bar)
        if target.position not in {-1, 0, 1}:
            raise ValueError("target position must be -1, 0, or 1")
        self.bar_index += 1
        return target

    def reset(self) -> None:
        self.position = 0
        self.bar_index = 0
        for indicator in self._indicators:
            indicator.reset()
