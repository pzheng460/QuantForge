from __future__ import annotations

import asyncio
from collections.abc import Callable

from quantforge.strategy.bar import Bar


class PollingBarFeed:
    """Poll a synchronous OHLCV loader and emit each closed bar once."""

    def __init__(
        self,
        loader: Callable[[], list[list]],
        *,
        poll_seconds: float = 5,
    ) -> None:
        self.loader = loader
        self.poll_seconds = poll_seconds
        self._last_timestamp = -1

    @staticmethod
    def _bar(row: list) -> Bar:
        return Bar(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )

    async def warmup(self, bars: int) -> list[Bar]:
        rows = await asyncio.to_thread(self.loader)
        selected = rows[-bars:]
        if selected:
            self._last_timestamp = int(selected[-1][0])
        return [self._bar(row) for row in selected]

    async def next_bar(self) -> Bar:
        while True:
            rows = await asyncio.to_thread(self.loader)
            for row in rows:
                if int(row[0]) > self._last_timestamp:
                    self._last_timestamp = int(row[0])
                    return self._bar(row)
            await asyncio.sleep(self.poll_seconds)
