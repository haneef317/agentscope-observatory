"""
Redis-backed publish/subscribe layer for real-time trace broadcasting.

Flow:

    TracingManager  --(redis.publish)-->  channel "run:{run_id}"
                                               |
                                  FastAPI WebSocket  --(redis.subscribe)--> browser

Redis is used rather than FastAPI's in-memory `manager` because it decouples
event producers (the agent worker) from consumers (WebSocket connections),
survives worker restarts, and allows multiple backend replicas to fan out
events to a shared set of connected clients.

When Redis is unavailable the module falls back to an in-memory asyncio
`Queue` based bus so the platform keeps working for local demos.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

log = logging.getLogger(__name__)


class InMemoryBus:
    """Fallback broadcast bus used when Redis is unreachable."""

    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def publish(self, channel: str, payload: dict) -> None:
        message = json.dumps(payload)
        for q in list(self._queues.get(channel, set())):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._queues[channel].add(queue)

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._queues.get(channel, set()).discard(queue)


class RedisPubSub:
    """Thin async wrapper around an aioredis PubSub client."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._pub = None
        self._sub = None
        self._pubsub = None
        self._ready = asyncio.Event()

    async def connect(self) -> bool:
        try:
            import redis.asyncio as aioredis

            self._pub = aioredis.from_url(self._url, decode_responses=True)
            self._sub = aioredis.from_url(self._url, decode_responses=True)
            await self._pub.ping()
            self._ready.set()
            return True
        except Exception as exc:  # pragma: no cover - network errors
            log.warning("Redis unavailable, using in-memory bus: %s", exc)
            self._pub = None
            self._sub = None
            return False

    async def publish(self, channel: str, payload: dict) -> None:
        if self._pub is not None:
            await self._pub.publish(channel, json.dumps(payload))

    async def subscribe(self, channel: str, queue: asyncio.Queue) -> None:
        await self._ready.wait()
        if self._sub is None:
            return
        # Lazily create one PubSub handle per RedisPubSub instance and reuse
        # it for every channel (a Redis client can own only one pubsub).
        if self._pubsub is None:
            self._pubsub = self._sub.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(channel)
        # A dedicated task pumps subscribed messages into the given queue.
        task = asyncio.create_task(self._pump(channel, queue))
        task.add_done_callback(lambda t: log.debug("pump done %s", channel))

    async def unsubscribe(self, channel: str) -> None:
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(channel)
            try:
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

    async def _pump(self, channel: str, queue: asyncio.Queue) -> None:
        try:
            async for message in self._pubsub.listen():
                if message and message.get("data"):
                    try:
                        queue.put_nowait(message["data"])
                    except asyncio.QueueFull:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("redis pump error on %s: %s", channel, exc)
