"""Who is watching which fight, and pushing to them.

Each open fight page holds a WebSocket. When anything happens in a fight
(a command, a round played, the timer changing) the server pushes it to
every socket on that fight: no polling by the page.

Several servers (a cluster, see README "Running several servers"): a push goes to this server's own sockets at once and, when the
channels have a database, is also written to the ``fight_events`` table. Every server reads the rows the others wrote about
twice a second (``Settings.fight_event_poll_seconds``) and sends them to its own sockets. That works on every database with no
new service; a broker such as Redis could later be a second implementation of this same interface.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket

from .. import wallclock
from .models import FightEvent

log = logging.getLogger(__name__)

#: How long a written event is kept (seconds). Far longer than the poll interval; a page that reconnects can catch up from it.
KEEP_SECONDS = 600
#: A row may become visible after one with a higher id (two servers insert at once and commit in the other order), so each poll
#: looks this many ids back and skips the rows it has already sent.
LOOK_BACK = 200
BATCH = 500


class FightChannels:
    def __init__(self, sessionmaker: async_sessionmaker | None = None) -> None:
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._sessionmaker = sessionmaker  # None: one server, nothing is written
        self.server = uuid4().hex
        self._last = 0  # the highest event id seen
        self._seen: set[int] = set()  # the ids at or above _last - LOOK_BACK already sent

    def attach(self, sessionmaker: async_sessionmaker) -> None:
        """Gives the channels the database to pass pushes through (the app does when the settings name a poll interval)."""
        self._sessionmaker = sessionmaker

    def join(self, fight_id: int, socket: WebSocket) -> None:
        self._sockets[fight_id].add(socket)

    def leave(self, fight_id: int, socket: WebSocket) -> None:
        sockets = self._sockets.get(fight_id)
        if sockets is not None:
            sockets.discard(socket)
            if not sockets:
                del self._sockets[fight_id]

    def watching(self, fight_id: int) -> int:
        """How many sockets on this server watch the fight."""
        return len(self._sockets.get(fight_id, ()))

    async def push(self, fight_id: int, message: dict[str, Any]) -> None:
        """Sends $message to this server's sockets at once, and leaves it for the other servers when there are any."""
        await self._deliver(fight_id, message)
        if self._sessionmaker is None:
            return
        try:
            async with self._sessionmaker() as session, session.begin():
                session.add(FightEvent(fight_id=fight_id, origin=self.server, message=message))
        except Exception:  # the local watchers have it; one lost cross-server push must not fail the call that made it
            log.exception("writing a fight event failed")

    async def _deliver(self, fight_id: int, message: dict[str, Any]) -> None:
        sockets = list(self._sockets.get(fight_id, ()))
        results = await asyncio.gather(*(socket.send_json(message) for socket in sockets), return_exceptions=True)
        for socket, result in zip(sockets, results):
            if isinstance(result, Exception):
                self.leave(fight_id, socket)  # gone: closed tab, dropped connection

    async def start(self) -> None:
        """Begins after the events already written, so a server that starts later does not replay old pushes."""
        if self._sessionmaker is None:
            return
        async with self._sessionmaker() as session:
            self._last = (await session.scalar(select(FightEvent.id).order_by(FightEvent.id.desc()).limit(1))) or 0
            # (the polls look back, so what is there now counts as sent)
            self._seen = set((await session.scalars(select(FightEvent.id).where(FightEvent.id > self._last - LOOK_BACK))).all())

    async def poll(self) -> int:
        """Sends the sockets here the events other servers wrote since the last poll; returns how many it sent."""
        if self._sessionmaker is None:
            return 0
        floor = max(0, self._last - LOOK_BACK)
        async with self._sessionmaker() as session:
            rows = (await session.scalars(select(FightEvent).where(FightEvent.id > floor).order_by(FightEvent.id).limit(BATCH))).all()
        sent = 0
        for row in rows:
            self._last = max(self._last, row.id)
            if row.id in self._seen:
                continue
            self._seen.add(row.id)
            if row.origin != self.server:
                await self._deliver(row.fight_id, row.message)
                sent += 1
        floor = max(0, self._last - LOOK_BACK)
        self._seen = {each for each in self._seen if each > floor}
        return sent


async def prune(session: AsyncSession, now=None) -> int:
    """Deletes the events no server can still be waiting for."""
    cutoff = (now or wallclock.now()) - timedelta(seconds=KEEP_SECONDS)
    return (await session.execute(delete(FightEvent).where(FightEvent.created_at < cutoff))).rowcount


async def poll_forever(channels: FightChannels, seconds: float) -> None:
    """Polls every $seconds for as long as the app runs."""
    await channels.start()
    while True:
        await asyncio.sleep(seconds)
        try:
            await channels.poll()
        except Exception:  # one bad pass must not stop the listener
            log.exception("reading the fight events failed")
