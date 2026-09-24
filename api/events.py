import asyncio
import json
import shutil
import time
from pathlib import Path


class EventHub:
    """In-process WebSocket fan-out plus small persistent trade-media cache."""

    def __init__(self, media_dir=None):
        self._clients = set()
        self._lock = asyncio.Lock()
        self.media_dir = Path(media_dir or "storage/t4bot_media")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._trade_media = {}

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
