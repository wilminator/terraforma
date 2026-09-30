"""Who is watching which fight, and pushing to them.

Each open fight page holds a WebSocket. When anything happens in a fight
(a command, a round played, the timer changing) the server pushes it to
every socket on that fight: no polling.

One process for now. Running several processes later means a shared
broker (Postgres LISTEN/NOTIFY or Redis) behind this same interface.
"""

import asyncio
from collections import defaultdict
from typing import Any

from starlette.websockets import WebSocket


class FightChannels:
    def __init__(self) -> None:
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)

    def join(self, fight_id: int, socket: WebSocket) -> None:
        self._sockets[fight_id].add(socket)

    def leave(self, fight_id: int, socket: WebSocket) -> None:
        sockets = self._sockets.get(fight_id)
        if sockets is not None:
            sockets.discard(socket)
            if not sockets:
                del self._sockets[fight_id]

    def watching(self, fight_id: int) -> int:
        return len(self._sockets.get(fight_id, ()))

    async def push(self, fight_id: int, message: dict[str, Any]) -> None:
        sockets = list(self._sockets.get(fight_id, ()))
        results = await asyncio.gather(*(socket.send_json(message) for socket in sockets), return_exceptions=True)
        for socket, result in zip(sockets, results):
            if isinstance(result, Exception):
                self.leave(fight_id, socket)  # gone: closed tab, dropped connection
