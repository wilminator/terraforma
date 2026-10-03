"""Pending drops: drops the game holds for need/want rolls or a hand-out, the table that keeps them, and the calls."""

import pytest
from sqlalchemy import func, select

from terraforma.content.loader import load_content
from terraforma.fights import drops, live, pending, store
from terraforma.fights.combatant import Command
from terraforma.fights.drops import ASSIGN, NEED_WANT, DropEntry
from terraforma.fights.events import Event
from terraforma.fights.models import FightRecord, PendingDrop, PendingDropChoice
from terraforma.fights.replay import apply_events
from terraforma.fights.rules import Rules
from terraforma.heroes import inventory, service
from terraforma.heroes.models import Hero
from terraforma.models import Account

pytestmark = pytest.mark.anyio

from .helpers import expect
from .test_drops import PESTS, POTION, RULES, hero_fight, kill, listing, plain, stacks
from .test_fight_rules import Scripted
from .test_fight_store import a_hero
from .test_trading import game, pair  # noqa: F401  (fixtures)


class Holding(Rules):
    """A game that holds every drop to be rolled for, or handed out."""

    mode = NEED_WANT

    def drop_mode(self, fight, party, monsters, share, rng):
        return self.mode


class Leading(Holding):
    """A game whose party leader is the lowest hero id, and that hands drops out."""

    mode = ASSIGN

    def may_assign_drop(self, hero_ids, hero_id):
        return hero_id == min(hero_ids)


# --- the rules: a drop is held instead of given -------------------------------------------------------------------

def test_the_engine_gives_every_drop_at_once_and_a_game_can_hold_them():
    table = plain(DropEntry(POTION, chance=10000, minimum=2, maximum=2))
    fight = hero_fight([table])
    kill(fight, (1, 0, 0))
    assert Rules().drop_mode(fight, 0, [(1, 0, 0)], "one", Scripted()) == drops.AUTO
    assert not Rules().may_assign_drop([1, 2], 1), "the engine has no party leader"
    events = Holding().roll_drops(fight, Scripted(1))
    assert listing(events) == [("DropHeld", 0, "potion", 2, "need_want")]
    assert fight.get((0, 0, 0)).inventory == [] and fight.get((0, 0, 1)).inventory == [], "nobody was given it"
    before = hero_fight([table])
    apply_events(before, RULES, [Event.from_list(each.to_list()) for each in events])  # the log replays with it in


# --- in the database -----------------------------------------------------------------------------------------------------

async def pair_fight(db, rules):
    """Aria and Bram on one team, fighting a pest that drops two potions: won, with the drop held. Returns the heroes."""
    aria = await a_hero(db)
    aria_id, account_id = aria.id, aria.account_id
    await load_content(db, PESTS)  # (this expires what the session holds)
    aria, mike = await db.get(Hero, aria_id), await db.get(Account, account_id)
    bram = await service.create_hero(db, mike, "Bram", "fighter")
    team = await service.create_team(db, mike, "Alpha")
    for hero in (aria, bram):
        await service.add_to_team(db, mike, team.id, hero.id)
    record = await live.start_team_fight(db, team, ["pest"], rules)
    for _ in range(30):
        if record.finished:
            return aria, bram, record
        await live.submit_command(db, record, mike.id, (0, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0), rules)
        await live.resolve_round(db, record, rules)
    raise AssertionError("the fight went on too long")


async def held(db, record):
    return list((await db.scalars(select(PendingDrop).where(PendingDrop.fight_id == record.id))).all())


async def held_in(db, fight_id):
    return list((await db.scalars(select(PendingDrop).where(PendingDrop.fight_id == fight_id))).all())


async def test_a_held_drop_becomes_one_pending_drop_that_nobody_has_been_given(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    assert (drop.item_key, drop.qty, drop.mode, drop.status, drop.party) == ("potion", 2, "need_want", "open", 0)
    assert (drop.map_id, drop.x, drop.y) == (record.map_id, record.x, record.y), "it stands where the fight did"
    assert await stacks(db, aria) == [("sword", 1), ("potion", 3)] and await stacks(db, bram) == []
    assert await pending.heroes_of(db, drop) == sorted([aria.id, bram.id])
    await store.apply_results(db, record, Holding())
    assert len(await held(db, record)) == 1, "saving the result again makes no second one"
    assert [each.id for each in await pending.for_hero(db, bram)] == [drop.id]


async def test_need_beats_want_and_the_drop_waits_for_everyone(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await pending.choose(db, bram, drop.id, "need")
    assert drop.status == "open", "Aria has not answered"
    await pending.choose(db, aria, drop.id, "want")
    assert (drop.status, drop.winner_id, drop.lost) == ("awarded", bram.id, 0)
    assert await stacks(db, bram) == [("potion", 2)] and await stacks(db, aria) == [("sword", 1), ("potion", 3)]
    rolls = {each.hero_id: each.roll for each in (await db.scalars(select(PendingDropChoice))).all()}
    assert rolls[bram.id] in range(1, 101) and rolls[aria.id] is None, "only the one who needed rolled"
    assert [each.id for each in await pending.for_hero(db, bram)] == [], "it is settled"


async def test_when_both_need_the_higher_roll_wins_and_a_tie_goes_to_the_lower_hero_id(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await pending.choose(db, aria, drop.id, "need")
    await pending.choose(db, bram, drop.id, "need")
    rows = {each.hero_id: each.roll for each in (await db.scalars(select(PendingDropChoice))).all()}
    assert all(roll in range(1, 101) for roll in rows.values())
    best = max(rows.values())
    assert drop.winner_id == min(hero for hero, roll in rows.items() if roll == best), "never luck: the best roll wins"
    assert drop.status == "awarded"


async def test_a_hero_may_change_their_mind_until_the_last_answers_and_everyone_passing_leaves_it_unclaimed(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await pending.choose(db, aria, drop.id, "need")
    await pending.choose(db, aria, drop.id, "pass")
    assert await db.scalar(select(func.count()).select_from(PendingDropChoice)) == 1, "one answer each"
    await pending.choose(db, bram, drop.id, "pass")
    assert (drop.status, drop.winner_id) == ("unclaimed", None)
    assert await stacks(db, bram) == []
    with pytest.raises(pending.PendingError, match="settled"):
        await pending.choose(db, aria, drop.id, "need")


async def test_only_a_hero_of_the_party_can_see_or_answer_and_a_bad_answer_is_refused(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    mike = await db.get(Account, aria.account_id)
    stranger = await service.create_hero(db, mike, "Cade", "fighter")
    assert await pending.for_hero(db, stranger) == []
    with pytest.raises(pending.PendingError, match="no such drop"):
        await pending.choose(db, stranger, drop.id, "need")
    with pytest.raises(pending.PendingError, match="no such drop"):
        await pending.choose(db, aria, drop.id + 99, "need")
    with pytest.raises(pending.PendingError, match="need, want or pass"):
        await pending.choose(db, aria, drop.id, "greed")
    with pytest.raises(pending.PendingError, match="not handed out"):
        await pending.assign(db, Leading(), aria, drop.id, bram.id)


async def test_what_does_not_fit_the_winners_pack_is_counted_not_lost_silently(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    for number in range(12):
        await inventory.add_item(db, bram, "sword", 1)  # gear never stacks: the pack is full
    await pending.choose(db, aria, drop.id, "pass")
    await pending.choose(db, bram, drop.id, "need")
    assert (drop.status, drop.winner_id, drop.lost) == ("awarded", bram.id, 2)


async def test_the_game_says_who_may_hand_a_drop_out_and_to_whom(db):
    aria, bram, record = await pair_fight(db, Leading())
    (drop,) = await held(db, record)
    assert drop.mode == "assign"
    with pytest.raises(pending.PendingError, match="can't hand"):
        await pending.assign(db, Leading(), max(aria, bram, key=lambda hero: hero.id), drop.id, aria.id)
    with pytest.raises(pending.PendingError, match="can't hand"):
        await pending.assign(db, Rules(), aria, drop.id, bram.id), "the engine has no leader"
    leader = min(aria, bram, key=lambda hero: hero.id)
    with pytest.raises(pending.PendingError, match="party's heroes"):
        await pending.assign(db, Leading(), leader, drop.id, 999999)
    with pytest.raises(pending.PendingError, match="not rolled for"):
        await pending.choose(db, leader, drop.id, "need")
    await pending.assign(db, Leading(), leader, drop.id, bram.id)
    assert (drop.status, drop.winner_id) == ("awarded", bram.id)
    assert await stacks(db, bram) == [("potion", 2)]
    assert (await pending.view(db, drop, bram))["winner_id"] == bram.id


async def test_a_hero_sees_their_own_answer_but_not_the_others_until_it_is_settled(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await pending.choose(db, aria, drop.id, "want")
    seen = await pending.view(db, drop, bram)
    assert (seen["your_choice"], seen["status"]) == (None, "open") and "rolls" not in seen
    assert (await pending.view(db, drop, aria))["your_choice"] == "want"
    await pending.choose(db, bram, drop.id, "want")
    assert {each["hero_id"] for each in (await pending.view(db, drop, bram))["rolls"]} == {aria.id, bram.id}


# --- the calls -----------------------------------------------------------------------------------------------------------

def test_the_calls_need_a_login_and_a_token_and_take_strict_bodies(pair):
    client, mike, aria, bram, zara = pair
    base = f"/api/heroes/{aria}/pending-drops"
    assert expect(client.get(base), 200).json() == []
    expect(client.post(f"{base}/1/choose", json={"choice": "need"}), 403)  # no CSRF token
    expect(client.post(f"{base}/1/choose", json={"choice": "greed"}, headers=mike), 422)
    expect(client.post(f"{base}/1/choose", json={"choice": "need", "more": 1}, headers=mike), 422)
    expect(client.post(f"{base}/1/assign", json={"to_hero_id": "x"}, headers=mike), 422)
    assert expect(client.post(f"{base}/1/choose", json={"choice": "need"}, headers=mike), 409).json()["detail"] == "there's no such drop for this hero"
    expect(client.get(f"/api/heroes/{zara}/pending-drops"), 404)  # not Mike's hero
    client.post("/api/logout", headers=mike)
    expect(client.get(base), 401)


# --- housekeeping: a drop left undecided too long -------------------------------------------------------------------------

async def age(db, drop, seconds):
    from datetime import timedelta

    from terraforma import wallclock

    drop.created_at = wallclock.now() - timedelta(seconds=seconds)  # (the tests' clock is not the real one)
    await db.flush()


def timeout(seconds):
    from terraforma.settings import Settings

    return Settings(session_secret="x" * 32, pending_drop_timeout_seconds=seconds)


async def test_the_timeout_is_off_by_default_so_a_drop_waits_for_ever(db):
    from terraforma import housekeeping

    assert timeout(0).pending_drop_timeout_seconds == 0 and "pending_drop_timeout_seconds" in housekeeping.stale_pending_drops.__doc__
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await age(db, drop, 10**7)
    assert await housekeeping.stale_pending_drops(db, 0, timeout(0)) == 0
    assert drop.status == "open"


async def test_a_drop_that_has_not_waited_long_enough_is_left_alone(db):
    from terraforma import housekeeping

    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await age(db, drop, 30)
    assert await housekeeping.stale_pending_drops(db, 0, timeout(60)) == 0 and drop.status == "open"


async def test_a_stale_need_want_drop_is_settled_with_the_answers_so_far(db):
    from terraforma import housekeeping

    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await pending.choose(db, aria, drop.id, "want")  # Bram never answers
    await age(db, drop, 120)
    assert await housekeeping.stale_pending_drops(db, 0, timeout(60)) == 1
    assert (drop.status, drop.winner_id) == ("awarded", aria.id), "the one who answered gets it"
    assert await stacks(db, aria) == [("sword", 1), ("potion", 5)]
    assert await housekeeping.stale_pending_drops(db, 0, timeout(60)) == 0, "settled once"


async def test_a_stale_drop_nobody_answered_or_nobody_handed_out_is_unclaimed(db):
    from terraforma import housekeeping

    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    await age(db, drop, 120)
    await housekeeping.stale_pending_drops(db, 0, timeout(60))
    assert (drop.status, drop.winner_id) == ("unclaimed", None)
    assert await stacks(db, bram) == []


async def test_a_stale_hand_out_is_unclaimed(db):
    from terraforma import housekeeping

    aria, bram, record = await pair_fight(db, Leading())
    (drop,) = await held(db, record)
    await age(db, drop, 120)
    assert await housekeeping.stale_pending_drops(db, 0, timeout(60)) == 1
    assert (drop.status, drop.winner_id) == ("unclaimed", None)


async def test_deleting_a_hero_clears_their_answers_and_leaves_the_drops_they_won_without_a_winner(db):
    aria = await a_hero(db)
    mike = await db.get(Account, aria.account_id)
    bram = await service.create_hero(db, mike, "Bram", "fighter")
    aria_id, bram_id = aria.id, bram.id
    record = FightRecord(guid="d" * 32, initial_state={}, map_id=aria.map_id, finished=True)
    db.add(record)
    await db.flush()
    won = PendingDrop(fight_id=record.id, number=0, party=0, item_key="potion", qty=1, mode="need_want", status="awarded", winner_id=bram_id, map_id=record.map_id)
    open_one = PendingDrop(fight_id=record.id, number=1, party=0, item_key="potion", qty=1, mode="need_want", status="open", map_id=record.map_id)
    db.add_all([won, open_one])
    await db.flush()
    db.add_all([PendingDropChoice(pending_id=won.id, hero_id=bram_id, choice="need", roll=7),
                PendingDropChoice(pending_id=open_one.id, hero_id=bram_id, choice="want"),
                PendingDropChoice(pending_id=open_one.id, hero_id=aria_id, choice="want")])
    await db.flush()
    won_id, open_id = won.id, open_one.id
    await service.delete_hero(db, mike, bram_id)
    db.expire_all()
    assert await db.get(Hero, bram_id) is None
    choices = (await db.scalars(select(PendingDropChoice))).all()
    assert [(each.pending_id, each.hero_id) for each in choices] == [(open_id, aria_id)], "only the other hero's answer is left"
    assert (await db.get(PendingDrop, won_id)).winner_id is None, "the drop stays on record, without its winner"
    assert (await db.get(PendingDrop, won_id)).status == "awarded"
    assert (await db.get(PendingDrop, open_id)).status == "open"


async def test_a_hero_who_fought_can_be_deleted_and_the_drop_stops_waiting_for_them(db):
    aria, bram, record = await pair_fight(db, Holding())
    (drop,) = await held(db, record)
    mike = await db.get(Account, aria.account_id)
    aria_id, bram_id, fight_id = aria.id, bram.id, record.id
    await pending.choose(db, bram, drop.id, "need")
    assert drop.status == "open", "Aria has not answered"
    await service.delete_hero(db, mike, aria_id)  # Aria fought, and the fight is over
    db.expire_all()
    drop = (await held_in(db, fight_id))[0]
    assert (drop.status, drop.winner_id) == ("awarded", bram_id), "Bram was the only one left to wait for, and he had answered"
    assert await db.get(Hero, aria_id) is None
    assert await pending.heroes_of(db, drop) == [bram_id]


async def test_a_drop_nobody_is_left_for_is_unclaimed_and_the_fight_stays_on_record(db):
    aria, bram, record = await pair_fight(db, Holding())
    mike = await db.get(Account, aria.account_id)
    mike_id, aria_id, bram_id, record_id = mike.id, aria.id, bram.id, record.id
    await service.delete_hero(db, mike, aria_id)
    db.expire_all()
    (drop,) = await held_in(db, record_id)
    assert drop.status == "open", "Bram is still to answer"
    mike = await db.get(Account, mike_id)
    await service.delete_hero(db, mike, bram_id)
    db.expire_all()
    (drop,) = await held_in(db, record_id)
    assert (drop.status, drop.winner_id) == ("unclaimed", None)
    assert await db.get(FightRecord, record_id) is not None, "the fight itself is history and stays"


async def test_a_hero_in_a_running_fight_cannot_be_deleted(db):
    aria, _bram, record = await pair_fight(db, Holding())
    mike = await db.get(Account, aria.account_id)
    aria_id = aria.id
    record.finished = False  # as if it were still being fought
    await db.flush()
    with pytest.raises(service.HeroError, match="is in a fight"):
        await service.delete_hero(db, mike, aria_id)
    assert await db.get(Hero, aria_id) is not None
