"""Fight pushes between servers: a push reaches the sockets on its own server at once and the other servers' sockets through the
database table, once each. Two channel objects on one database stand for two servers. On every database."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import housekeeping, wallclock
from terraforma.db.session import make_sessionmaker
from terraforma.fights import channels as fight_channels
from terraforma.fights.channels import LOOK_BACK, FightChannels
from terraforma.fights.models import FightEvent
from terraforma.settings import Settings

pytestmark = [pytest.mark.anyio, pytest.mark.fights]


class Socket:
    """A watcher's WebSocket: keeps what it is sent."""

    def __init__(self, broken=False):
        self.got, self.broken = [], broken

    async def send_json(self, message):
        if self.broken:
            raise RuntimeError("closed")
        self.got.append(message)


async def two_servers(engine):
    maker = make_sessionmaker(engine)
    first, second = FightChannels(maker), FightChannels(maker)
    await first.start()
    await second.start()
    return maker, first, second


async def test_a_push_reaches_the_sockets_here_at_once_and_those_on_another_server_after_a_poll(engine):
    _maker, first, second = await two_servers(engine)
    here, there, other_fight = Socket(), Socket(), Socket()
    first.join(1, here)
    second.join(1, there)
    second.join(2, other_fight)
    await first.push(1, {"type": "round"})
    assert here.got == [{"type": "round"}] and there.got == []
    assert await second.poll() == 1
    assert there.got == [{"type": "round"}] and other_fight.got == []
    assert await first.poll() == 0 and here.got == [{"type": "round"}]  # a server does not hear its own push again
    assert await second.poll() == 0 and there.got == [{"type": "round"}]  # nor does one hear a push twice


async def test_pushes_arrive_in_order_from_every_server(engine):
    _maker, first, second = await two_servers(engine)
    third = FightChannels(make_sessionmaker(engine))
    await third.start()
    watcher = Socket()
    third.join(5, watcher)
    await first.push(5, {"n": 1})
    await second.push(5, {"n": 2})
    await first.push(5, {"n": 3})
    await third.poll()
    assert [m["n"] for m in watcher.got] == [1, 2, 3]


async def test_a_server_that_starts_later_does_not_replay_old_pushes(engine):
    maker, first, _second = await two_servers(engine)
    await first.push(1, {"type": "old"})
    late = FightChannels(maker)
    await late.start()
    watcher = Socket()
    late.join(1, watcher)
    assert await late.poll() == 0 and watcher.got == []
    await first.push(1, {"type": "new"})
    await late.poll()
    assert watcher.got == [{"type": "new"}]


async def test_a_row_that_becomes_visible_late_is_still_sent(engine):
    """Two servers insert at once and the one with the higher id commits first: the poll that saw the higher id must still send the lower."""
    maker, first, second = await two_servers(engine)
    watcher = Socket()
    second.join(1, watcher)
    async with maker() as session, session.begin():
        session.add(FightEvent(id=2, fight_id=1, origin=first.server, message={"n": 2}))
    await second.poll()
    async with maker() as session, session.begin():
        session.add(FightEvent(id=1, fight_id=1, origin=first.server, message={"n": 1}))
    await second.poll()
    await second.poll()
    assert sorted(m["n"] for m in watcher.got) == [1, 2] and len(watcher.got) == 2


async def test_the_poll_looks_back_only_so_far(engine):
    maker, first, second = await two_servers(engine)
    async with maker() as session, session.begin():
        session.add_all(FightEvent(fight_id=1, origin=first.server, message={"n": n}) for n in range(LOOK_BACK + 5))
    await second.poll()
    await second.poll()
    assert len(second._seen) <= LOOK_BACK + 1  # what it remembers stays bounded


async def test_a_dropped_socket_is_forgotten_and_the_others_still_get_the_push(engine):
    _maker, first, _second = await two_servers(engine)
    gone, there = Socket(broken=True), Socket()
    first.join(1, gone)
    first.join(1, there)
    await first.push(1, {"type": "round"})
    assert there.got == [{"type": "round"}] and first.watching(1) == 1


async def test_one_server_writes_nothing(db):
    alone = FightChannels()
    watcher = Socket()
    alone.join(1, watcher)
    await alone.push(1, {"type": "round"})
    assert watcher.got == [{"type": "round"}] and await alone.poll() == 0
    assert await db.scalar(select(func.count()).select_from(FightEvent)) == 0


async def test_a_failed_write_does_not_stop_the_local_push(engine):
    class Broken:
        def __call__(self):
            raise RuntimeError("database down")

    alone = FightChannels(Broken())
    watcher = Socket()
    alone.join(1, watcher)
    await alone.push(1, {"type": "round"})
    assert watcher.got == [{"type": "round"}]


async def test_housekeeping_removes_old_events_and_keeps_new_ones(engine, db):
    _maker, first, _second = await two_servers(engine)
    await first.push(1, {"n": 1})
    assert await housekeeping.old_fight_events(db, 0, Settings.model_construct()) == 0
    await db.commit()
    assert await fight_channels.prune(db, wallclock.now() + timedelta(seconds=fight_channels.KEEP_SECONDS + 1)) == 1
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(FightEvent)) == 0


# --- the app -------------------------------------------------------------------------------------------------------------

def test_the_poll_interval_is_off_by_default():
    assert Settings(session_secret="x" * 32).fight_event_poll_seconds == 0


def test_an_app_with_a_poll_interval_passes_its_pushes_through_the_database(database_url, tmp_path):
    """Pushes are written only when the settings turn the cluster listener on; the listener runs and stops with the app."""
    from starlette.testclient import TestClient

    from terraforma.app import create_app
    from terraforma.keys import new_key
    from terraforma.testing import SECRET, fresh_database, make_engine, run

    async def prepare():
        engine = make_engine(database_url)
        await fresh_database(engine, database_url)
        await engine.dispose()

    run(prepare())
    new_key(tmp_path / "keys")
    base = dict(database_url=database_url, session_secret=SECRET, secure_cookies=False, public_url="http://game.test", key_dir=tmp_path / "keys",
                fight_timer_seconds=0, housekeeping_seconds=0)

    async def count(app):
        async with app.state.sessionmaker() as session:
            return await session.scalar(select(func.count()).select_from(FightEvent))

    for poll, written in ((0, 0), (0.05, 1)):
        app = create_app(Settings(**base, fight_event_poll_seconds=poll))
        with TestClient(app) as client:
            client.portal.call(app.state.fights.push, 1, {"type": "round"})
            assert client.portal.call(count, app) == written
