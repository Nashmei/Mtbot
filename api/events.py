import asyncio
import json
import time


class EventHub:
    """Small in-process WebSocket fan-out used as an invalidation/event channel."""

    def __init__(self):
        self._clients = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket):
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket):
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, event_type, payload=None):
        message = json.dumps(
            {
                'type': str(event_type),
                'ts': time.time(),
                'payload': payload or {},
            },
            ensure_ascii=False,
            default=str,
        )

        async with self._lock:
            clients = tuple(self._clients)

        stale = []
        for websocket in clients:
            try:
                await websocket.send_text(message)
            except Exception:
                stale.append(websocket)

        if stale:
            async with self._lock:
                for websocket in stale:
                    self._clients.discard(websocket)
