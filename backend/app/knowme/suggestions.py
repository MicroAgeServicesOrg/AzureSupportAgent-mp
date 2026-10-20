"""Bounded, deduplicated suggestion work shared by all Know-Me requests in a process."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("app.knowme.suggestions")
CONCURRENCY = 5
CACHE_TTL = 600
CACHE_LIMIT = 512


class SuggestionError(RuntimeError):
    """The provider failed or returned an invalid suggestion response."""


class _Pool:
    def __init__(self) -> None:
        self.gate = asyncio.Semaphore(CONCURRENCY)
        self.pending: dict[str, asyncio.Task[list[str]]] = {}
        self.cache: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()

    async def get(self, key: str, worker: Callable[[], Awaitable[list[str]]]) -> list[str]:
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            self.cache.move_to_end(key)
            return list(cached[1])
        self.cache.pop(key, None)
        task = self.pending.get(key)
        if task is None:
            async def run() -> list[str]:
                try:
                    async with self.gate:
                        result = await asyncio.wait_for(worker(), timeout=60)
                    self.cache[key] = (time.monotonic(), list(result))
                    while len(self.cache) > CACHE_LIMIT:
                        self.cache.popitem(last=False)
                    return result
                except Exception:
                    logger.exception("Know-Me suggestion work failed")
                    raise
                finally:
                    self.pending.pop(key, None)

            task = asyncio.create_task(run())
            # A disconnected subscriber must not cancel shared work for other subscribers.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self.pending[key] = task
        return list(await asyncio.shield(task))


_pools: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Pool] = weakref.WeakKeyDictionary()


async def cached_choices(
    scope: str, inputs: Any, worker: Callable[[], Awaitable[list[str]]],
) -> list[str]:
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    if pool is None:
        pool = _Pool()
        _pools[loop] = pool
    key = hashlib.sha256(json.dumps([scope, inputs], sort_keys=True).encode()).hexdigest()
    return await pool.get(key, worker)
