"""Servers sharing a database share the overdue rounds: a fight is claimed with a lock, so no round is played twice and a fight
another server is playing is left to it. On every database (the lock itself needs Postgres or MySQL; SQLite has one writer)."""

import pytest
from sqlalchemy import select

from terraforma.fights import live
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules

from .test_live_fights import Channels, started

pytestmark = [pytest.mark.anyio, pytest.mark.fights]

RULES = Rules()


@pytest.fixture
async def overdue(db, later):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _hero, _mike, _team, record = await started(db, "ogre")
    await db.commit()
    later(31)
    return async_sessionmaker(db.bind, expire_on_commit=False), record.id


async def test_a_claimed_fight_is_found_by_its_number(overdue):
    sessionmaker, fight_id = overdue
    async with sessionmaker() as session, session.begin():
        assert (await live.claim(session, fight_id)).id == fight_id
        assert await live.claim(session, fight_id + 1000) is None


async def test_a_fight_another_server_holds_is_skipped_not_waited_for(overdue, db):
    sessionmaker, fight_id = overdue
    if db.bind.dialect.name == "sqlite":
        pytest.skip("SQLite has no row locks: one writer at a time is its claim")
    channels = Channels()
    async with sessionmaker() as other, other.begin():
        assert await live.claim(other, fight_id) is not None  # another server is playing it
        assert await live.resolve_overdue(sessionmaker, RULES, None, channels) == 0
    assert channels.sent == []
    assert await live.resolve_overdue(sessionmaker, RULES, None, channels) == 1  # released: this server plays it
    assert [each[0] for each in channels.sent] == [fight_id]


async def test_a_round_is_played_once_however_many_servers_look(overdue):
    sessionmaker, fight_id = overdue
    first, second = Channels(), Channels()
    for channels in (first, second):  # (one after the other: the second finds the round already played)
        await live.resolve_overdue(sessionmaker, RULES, None, channels)
    assert len(first.sent) + len(second.sent) == 1
    async with sessionmaker() as session:
        record = (await session.scalars(select(FightRecord).where(FightRecord.id == fight_id))).one()
        assert live.aware(record.round_deadline) > live.wallclock.now()
