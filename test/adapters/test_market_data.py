"""PollingBarFeed stall-detection boundary tests.

``PollingBarFeed.next_bar`` polls a synchronous loader and emits each closed
bar once. If the loader keeps returning the same last timestamp forever
(e.g. an exchange whose OHLCV endpoint is wedged), the legacy behavior was to
poll indefinitely — silently hanging the live engine. The
``max_stall_seconds`` guard turns that silent hang into a loud ``RuntimeError``
so the engine can react (halt/alert) instead of looking alive while doing
nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from quantforge.adapters.market_data import PollingBarFeed

_ROWS = [
    [1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 10.0],
    [1_700_000_003_600_000, 100.5, 102.0, 100.0, 101.5, 12.0],
]


def _stalled_loader(rows=_ROWS):
    """A loader that always returns the SAME rows — never advances."""
    return lambda: list(rows)


def test_next_bar_raises_on_stalled_feed():
    """A loader stuck on the same last timestamp must raise RuntimeError after
    ``max_stall_seconds`` instead of polling forever."""
    feed = PollingBarFeed(
        _stalled_loader(),
        poll_seconds=0.01,
        max_stall_seconds=0.05,
    )
    # Warmup seeds _last_timestamp with the last row's timestamp; the stall
    # clock only starts on the first next_bar entry.
    warmup = asyncio.run(feed.warmup(len(_ROWS)))
    assert len(warmup) == 2

    with pytest.raises(RuntimeError, match="stalled"):
        asyncio.run(feed.next_bar())


def test_next_bar_advancing_feed_does_not_stall():
    """A feed that produces a newer bar on each poll never trips the guard."""
    state = {"ts": 1_700_000_003_600_000}

    def advancing_loader():
        # Each call returns a single row one bar newer than the last.
        state["ts"] += 3_600_000
        return [[state["ts"], 100.0, 101.0, 99.0, 100.5, 10.0]]

    feed = PollingBarFeed(
        advancing_loader,
        poll_seconds=0.01,
        max_stall_seconds=1.0,
    )
    asyncio.run(feed.warmup(0))  # no warmup bars -> _last_timestamp stays -1
    bar = asyncio.run(asyncio.wait_for(feed.next_bar(), timeout=2.0))
    assert bar.timestamp > 1_700_000_003_600_000


def test_default_max_stall_is_none_preserves_infinite_poll():
    """Backward compatibility: max_stall_seconds=None must NOT raise, even when
    the feed is stalled (legacy infinite-poll behavior). We assert this by
    patching asyncio.sleep to raise after the first poll so the test does not
    hang forever."""
    feed = PollingBarFeed(_stalled_loader(), poll_seconds=0.01)
    asyncio.run(feed.warmup(len(_ROWS)))

    async def _cancel_after_first_sleep():
        orig_sleep = asyncio.sleep

        async def _boom(_delay):
            raise asyncio.CancelledError("stop the infinite poll")

        asyncio.sleep = _boom  # type: ignore[assignment]
        try:
            with pytest.raises(asyncio.CancelledError, match="stop the infinite poll"):
                await feed.next_bar()
        finally:
            asyncio.sleep = orig_sleep  # type: ignore[assignment]

    asyncio.run(_cancel_after_first_sleep())


def test_warmup_not_subject_to_stall_limit():
    """Warmup must never trip the stall guard, even with a tiny deadline and a
    stalled loader — the clock only starts on the first next_bar entry."""
    feed = PollingBarFeed(
        _stalled_loader(),
        poll_seconds=0.01,
        max_stall_seconds=0.0,  # would trip instantly if warmup were watched
    )
    bars = asyncio.run(feed.warmup(len(_ROWS)))
    assert [b.timestamp for b in bars] == [r[0] for r in _ROWS]
