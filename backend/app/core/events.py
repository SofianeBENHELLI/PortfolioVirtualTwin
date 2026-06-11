"""In-process pub/sub for SSE. Fine for a single-process deployment (≤10 users)."""
import asyncio
import json
from typing import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: str, data: dict) -> None:
        """Thread-safe publish (background jobs run in worker threads)."""
        if self._loop is None:
            return
        payload = {"event": event, "data": data}
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, payload)

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                item = await q.get()
                yield json.dumps(item)
        finally:
            self._subscribers.discard(q)


bus = EventBus()
