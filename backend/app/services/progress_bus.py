"""Extraction progress event bus — in-memory with optional Redis pub/sub."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.redis_client import get_redis, ping_redis

CHANNEL_PREFIX = "fiber:extraction:"


class ProgressBus:
    """Broadcast extraction progress to SSE clients."""

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[dict[str, Any]]] = {}

    @staticmethod
    def channel(job_id: int) -> str:
        return f"{CHANNEL_PREFIX}{job_id}"

    def subscribe_local(self, job_id: int) -> asyncio.Queue[dict[str, Any]]:
        if job_id not in self._queues:
            self._queues[job_id] = asyncio.Queue(maxsize=64)
        return self._queues[job_id]

    def unsubscribe_local(self, job_id: int) -> None:
        self._queues.pop(job_id, None)

    def _push_local(self, job_id: int, payload: dict[str, Any]) -> None:
        queue = self._queues.get(job_id)
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    async def push(self, job_id: int, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        redis = await get_redis()
        if redis is not None:
            await redis.publish(self.channel(job_id), json.dumps(payload, ensure_ascii=False))
        else:
            self._push_local(job_id, payload)

    async def stream_events(self, job_id: int, timeout: float = 30.0):
        """Yield progress payloads, or None on heartbeat timeout."""
        redis = await get_redis()
        if redis is not None:
            pubsub = redis.pubsub()
            await pubsub.subscribe(self.channel(job_id))
            try:
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=timeout,
                    )
                    if message is None:
                        yield None
                        continue
                    if message.get("type") == "message":
                        data = message.get("data")
                        if isinstance(data, str):
                            yield json.loads(data)
                        else:
                            yield data
            finally:
                await pubsub.unsubscribe(self.channel(job_id))
                await pubsub.acclose()
            return

        queue = self.subscribe_local(job_id)
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                yield None

    async def ping(self) -> bool:
        return await ping_redis()

    async def close(self) -> None:
        return


progress_bus = ProgressBus()
