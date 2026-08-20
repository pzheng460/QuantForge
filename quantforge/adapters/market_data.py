from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from quantforge.strategy.bar import Bar


class PollingBarFeed:
    """Poll a synchronous OHLCV loader and emit each closed bar once."""

    def __init__(
        self,
        loader: Callable[[], list[list]],
        *,
        poll_seconds: float = 5,
        max_stall_seconds: float | None = None,
    ) -> None:
        self.loader = loader
        self.poll_seconds = poll_seconds
        # When set, next_bar raises RuntimeError if no bar with a newer
        # timestamp arrives within this many seconds — guards against a
        # stalled feed (loader returning the same last timestamp forever)
        # silently hanging the live engine. ``None`` keeps the legacy
        # infinite-poll behavior (backward compatible). Warmup is never
        # subject to this limit: waiting there is expected.
        self.max_stall_seconds = max_stall_seconds
        self._last_timestamp = -1
        # monotonic clock reading of the last time next_bar advanced (saw a
        # newer timestamp). Lazily set on first next_bar entry, so the stall
        # clock only runs during live polling — not warmup.
        self._last_progress_monotonic: float | None = None

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
        # Lazily start the stall clock on first next_bar entry. Warmup never
        # touches this timer, so a stalled feed is only fatal during live
        # polling — not during warmup, where waiting is expected.
        if self.max_stall_seconds is not None and self._last_progress_monotonic is None:
            self._last_progress_monotonic = time.monotonic()
        while True:
            rows = await asyncio.to_thread(self.loader)
            for row in rows:
                if int(row[0]) > self._last_timestamp:
                    self._last_timestamp = int(row[0])
                    self._last_progress_monotonic = time.monotonic()
                    return self._bar(row)
            if self._stalled():
                raise RuntimeError(
                    f"PollingBarFeed stalled: no new bar within "
                    f"{self.max_stall_seconds}s "
                    f"(last_timestamp={self._last_timestamp})"
                )
            await asyncio.sleep(self.poll_seconds)

    def _stalled(self) -> bool:
        """True when the stall deadline has elapsed since the last new bar.

        Returns False (no-op) when ``max_stall_seconds`` is None or the clock
        has not started yet, preserving the legacy infinite-poll behavior.
        """
        if self.max_stall_seconds is None or self._last_progress_monotonic is None:
            return False
        return (
            time.monotonic() - self._last_progress_monotonic
            > self.max_stall_seconds
        )
