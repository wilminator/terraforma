"""Bounties: a held reward for killing a team or a hero, paid once to the teams that did it, with no contract to accept."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from terraforma import wallclock
from terraforma.alliances import service as alliance_service
from terraforma.alliances.hooks import Alliances
from terraforma.alliances.models import Alliance
from terraforma.contracts import bounties
from terraforma.contracts.models import BOUNTY_EXPIRED, BOUNTY_OPEN, BOUNTY_PAID, BOUNTY_WITHDRAWN
from terraforma.economy import TeamGold
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.heroes.models import Team
from terraforma.storage.hooks import NoStorage

from .test_trading import account, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
STORAGE = NoStorage()
DAY = 24 * 3600


@pytest.fixture
def clock(monkeypatch):
    now = [wallclock.now()]
    monkeypatch.setattr(wallclock, "now", lambda: now[0])

    class Clock:
        @staticmethod
        def move(seconds):
            now[0] += timedelta(seconds=seconds)

    return Clock


async def cast(db):
    """Mike's Vanguard (Aria) posts; Zed's Scouts (Cleo and Dee) are the target; Dan's Hunters (Hex) and Eve's Rivals (Rex) can collect."""
    owners = {name: await account(db, name) for name in ("Mike", "Zed", "Dan", "Eve")}
    poster, (aria,) = await team_with(db, owners["Mike"], "Vanguard", ["Aria"])
    target, (cleo, dee) = await team_with(db, owners["Zed"], "Scouts", ["Cleo", "Dee"])
    hunters, (hex_,) = await team_with(db, owners["Dan"], "Hunters", ["Hex"])
    rivals, (rex,) = await team_with(db, owners["Eve"], "Rivals", ["Rex"])
    for team in (poster, target, hunters, rivals):
        await ECONOMY.credit_team(db, team.id, 1000)
    return poster, target, hunters, rivals, aria, cleo, dee, hex_, rex


async def gold_of(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


async def kill(db, killers, victim_index, victims, tag, fallen=True):
    """A fight just begun: the killers' heroes in party 0, the victims' in party 1; the victim at `victim_index` falls."""
    record = FightRecord(guid=f"{tag}{killers[0].id:015d}{victims[0].id:016d}", initial_state={}, map_id=victims[0].map_id, created_at=wallclock.now(), finished=True)
    db.add(record)
    await db.flush()
    for index, hero in enumerate(killers):
        db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=index, name=hero.name, hero_id=hero.id))
    for index, hero in enumerate(victims):
        db.add(FightParticipant(fight_id=record.id, party=1, group_index=0, character=index, name=hero.name, hero_id=hero.id))
    db.add(FightActionRecord(
        fight_id=record.id, sequence=1, commands=[], events=[["Died", [1, 0, victim_index, 10, 0]]] if fallen else [], previous_hash="0", hash="1",
    ))
    await db.flush()


async def posted(db, aria, target, scope="team", hero_id=None, reward=500, lifetime=DAY):
    return await bounties.post(db, ECONOMY, aria, "vanguard", target.id, scope, hero_id, reward, lifetime)


async def test_posting_holds_the_reward_and_the_bounty_is_listed(clock, db):
    poster, target, _h, _r, aria, *_rest = await cast(db)
    bounty = await posted(db, aria, target)
    assert bounty.state == BOUNTY_OPEN and await gold_of(db, poster) == 500
    assert [each.id for each in await bounties.open_bounties(db, "vanguard")] == [bounty.id]
    assert await bounties.open_bounties(db, "elsewhere") == []


async def test_a_bounty_that_cannot_be_posted_changes_nothing(clock, db):
    poster, target, _h, _r, aria, cleo, *_rest = await cast(db)
    for kwargs, match in (
        (dict(target=poster), "on itself"),
        (dict(scope="hero", hero_id=aria.id), "not on the target team"),
        (dict(scope="all"), "whole team or one hero"),
        (dict(reward=0), "1 gold or more"),
        (dict(reward=5000), "not enough"),
        (dict(lifetime=0), "1 gold or more"),
    ):
        with pytest.raises(bounties.BountyError, match=match):
            await posted(db, aria, kwargs.pop("target", target), **kwargs)
    assert await gold_of(db, poster) == 1000


async def test_killing_the_whole_team_pays_the_hunters_once(clock, db):
    poster, target, hunters, rivals, aria, cleo, dee, hex_, rex = await cast(db)
    bounty = await posted(db, aria, target)
    bounty_id = bounty.id
    await kill(db, [hex_], 0, [cleo, dee], "a")
    assert (await bounties.judge(db, ECONOMY, STORAGE, bounty_id)).state == BOUNTY_OPEN  # Dee still stands
    await kill(db, [hex_], 1, [cleo, dee], "b")
    paid = await bounties.judge(db, ECONOMY, STORAGE, bounty_id)
    assert paid.state == BOUNTY_PAID and paid.paid_to == [hunters.id]
    assert await gold_of(db, hunters) == 1500 and await gold_of(db, poster) == 500
    again = await bounties.judge(db, ECONOMY, STORAGE, bounty_id)
    assert again.state == BOUNTY_PAID and await gold_of(db, hunters) == 1500  # once
    await kill(db, [rex], 1, [cleo, dee], "c")  # a later kill earns nothing
    assert await gold_of(db, rivals) == 1000


async def test_a_bounty_on_one_hero_is_earned_by_that_hero_alone_and_shared_by_the_teams_that_fought(clock, db):
    _poster, target, hunters, rivals, aria, cleo, dee, hex_, rex = await cast(db)
    bounty = await posted(db, aria, target, scope="hero", hero_id=dee.id, reward=501)
    await kill(db, [hex_, rex], 0, [cleo, dee], "a")  # Cleo falls: not the named hero
    assert (await bounties.judge(db, ECONOMY, STORAGE, bounty.id)).state == BOUNTY_OPEN
    await kill(db, [hex_, rex], 1, [cleo, dee], "b")  # Dee falls, to both teams' heroes
    paid = await bounties.judge(db, ECONOMY, STORAGE, bounty.id)
    assert paid.state == BOUNTY_PAID and sorted(paid.paid_to) == sorted([hunters.id, rivals.id])
    assert sorted([await gold_of(db, hunters), await gold_of(db, rivals)]) == [1250, 1251]  # 501 split, the odd coin to the first


async def test_a_fall_to_monsters_or_before_the_posting_earns_nothing(clock, db):
    _poster, target, hunters, _rivals, aria, cleo, dee, hex_, _rex = await cast(db)
    await kill(db, [hex_], 0, [cleo, dee], "x")
    await kill(db, [hex_], 1, [cleo, dee], "y")  # both fall, before the posting
    clock.move(10)
    bounty = await posted(db, aria, target)
    assert (await bounties.judge(db, ECONOMY, STORAGE, bounty.id)).state == BOUNTY_OPEN
    assert await gold_of(db, hunters) == 1000


async def test_an_unclaimed_bounty_expires_or_is_withdrawn_and_the_reward_returns(clock, db):
    poster, target, _h, _r, aria, *_rest = await cast(db)
    stale = await posted(db, aria, target)
    clock.move(2 * DAY)
    assert (await bounties.judge(db, ECONOMY, STORAGE, stale.id)).state == BOUNTY_EXPIRED
    assert await gold_of(db, poster) == 1000
    taken_back = await posted(db, aria, target)
    taken_back_id = taken_back.id
    other = await bounties.withdraw(db, ECONOMY, STORAGE, aria, taken_back_id)
    assert other.state == BOUNTY_WITHDRAWN and await gold_of(db, poster) == 1000
    with pytest.raises(bounties.BountyError, match="settled already"):
        await bounties.withdraw(db, ECONOMY, STORAGE, aria, taken_back_id)


async def test_only_the_poster_can_withdraw(clock, db):
    _poster, target, _h, _r, aria, cleo, *_rest = await cast(db)
    bounty = await posted(db, aria, target)
    with pytest.raises(bounties.BountyError, match="no such bounty of yours"):
        await bounties.withdraw(db, ECONOMY, STORAGE, cleo, bounty.id)


async def test_an_alliance_can_post_from_its_treasury_and_gets_the_reward_back(clock, db):
    poster, target, hunters, _rivals, aria, *_rest = await cast(db)
    hooks = Alliances()
    await db.execute(update(Team).values(last_played_at=wallclock.timestamp()))
    alliance = await alliance_service.found(db, hooks, poster, "Pact")
    await alliance_service.deposit(db, hooks, alliance, poster.id, 400)
    bounty = await bounties.post(db, ECONOMY, aria, "vanguard", target.id, "team", None, 300, DAY, hooks, alliance.id)
    assert await db.scalar(select(Alliance.gold).where(Alliance.id == alliance.id)) == 100 and await gold_of(db, poster) == 600
    clock.move(2 * DAY)
    assert (await bounties.judge(db, ECONOMY, STORAGE, bounty.id)).state == BOUNTY_EXPIRED
    assert await db.scalar(select(Alliance.gold).where(Alliance.id == alliance.id)) == 400
    with pytest.raises(bounties.BountyError, match="doesn't hold that much"):
        await bounties.post(db, ECONOMY, aria, "vanguard", target.id, "team", None, 900, DAY, hooks, alliance.id)
