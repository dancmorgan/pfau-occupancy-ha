"""Retry a flaky fetch a few times before giving up.

Deliberately free of Home Assistant imports so it can be unit tested alone.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

_LOGGER = logging.getLogger(__name__)


async def retry_on[T](
    fetch: Callable[[], Awaitable[T]],
    retry_errors: type[Exception],
    delays: Sequence[float],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Await `fetch()`, retrying after each delay if it raises `retry_errors`.

    Makes len(delays) + 1 attempts in total. The final attempt's error
    propagates, as does any exception that isn't a `retry_errors`.
    """
    for delay in delays:
        try:
            return await fetch()
        except retry_errors as err:
            _LOGGER.debug("Fetch failed (%s); retrying in %ss", err, delay)
            await sleep(delay)
    return await fetch()
