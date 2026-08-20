"""Cooperative concurrency cap for CPU-bound backtest/optimize jobs.

``asyncio.to_thread`` runs each job on the default executor
(``min(32, cpu_count + 4)`` threads). A burst of legal parallel
backtests/optimizes can therefore saturate the whole pool and starve
event-loop work (HTTP callbacks, WS heartbeats). A single shared semaphore
caps how many CPU-bound jobs execute at once; excess jobs WAIT in a queue
instead of being dropped, so no client request is lost. ``check_cancelled``
is re-evaluated once a queued job acquires a slot, so a cancel requested
while queued still lands promptly.
"""

from __future__ import annotations

import asyncio

#: Maximum simultaneous CPU-bound backtest/optimize jobs. Deliberately well
#: below the default executor size so the event loop never starves behind a
#: batch of jobs. Backtests are single-threaded CPU work — more parallel slots
#: than cores adds no throughput, only contention.
MAX_PARALLEL_JOBS = 4

_semaphore = asyncio.Semaphore(MAX_PARALLEL_JOBS)


def replace_semaphore(limit: int) -> None:
    """Test hook: swap in a fresh semaphore (e.g. a tighter cap)."""
    global _semaphore
    _semaphore = asyncio.Semaphore(limit)


async def acquire() -> None:
    await _semaphore.acquire()


def release() -> None:
    _semaphore.release()
