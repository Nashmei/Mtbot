import asyncio
import shutil
import time
from pathlib import Path


class EventHub:
    """Single-writer WebSocket fan-out plus a small trade-media cache."""

    def __init__(self, media_dir=None, queue_size=128):
        self._clients = {}
        self._lock = asyncio.Lock()
        self._queue_size = max(8, int(queue_size))
        self.media_dir = Path(media_dir or "storage/t4bot_media")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._trade_media = {}

    async def connect(self, websocket):
        await websocket.accept()
        queue = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._clients[websocket] = queue
        return queue

    async def disconnect(self, websocket):
        async with self._lock:
            self._clients.pop(websocket, None)

    async def broadcast(self, event_type, payload=None):
        message = {
            "type": str(event_type),
            "ts": time.time(),
            "payload": payload or {},
        }

        async with self._lock:
            queues = tuple(self._clients.values())

        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def store_trade_media(self, ticket, source_path):
        try:
            ticket = int(ticket)
            source = Path(source_path)
            if ticket <= 0 or not source.is_file():
                return None
            target = self.media_dir / f"{ticket}.png"
            shutil.copyfile(source, target)
            self._trade_media[ticket] = target
            return str(ticket)
        except Exception:
            return None

    def media_path(self, media_id):
        try:
            ticket = int(media_id)
        except (TypeError, ValueError):
            return None

        path = self._trade_media.get(ticket) or (self.media_dir / f"{ticket}.png")
        return path if path.is_file() else None

    def media_id_for_ticket(self, ticket):
        try:
            ticket = int(ticket)
        except (TypeError, ValueError):
            return None

        path = self.media_path(ticket)
        return str(ticket) if path else None
