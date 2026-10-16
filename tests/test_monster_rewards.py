"""Monster-party play, slice c: what a seat earns when the fight ends (Challenge Tokens by the game's hook), the lockout for abandoning
a lost fight, and the heroes' bonus experience for beating a player's monsters. On every database."""

import math
from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.challenge import service as challenge
from terraforma.fights import live, store
from terraforma.fights.combatant import Command
from terraforma.fights.events import Event, EventType
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.fights.state import dehydrate, hydrate
from terraforma.monsterplay import service as monsters
from terraforma.monsterplay.models import MonsterLockout, MonsterSeat

from .test_monster_control import HERO, FOE, an_admin, hero_defends, monster_attacks, played_commands, seated
from .test_monster_play import RULES

pytestmark = [pytest.mark.anyio, pytest.mark.fights]


class Sure(Rules):
    """Every attack lands, so who wins is set by the hit points a test gives each side."""

    def chance_to_hit(self, rng, accuracy, dodge):
        return 50


class Pays(Sure):
    """Vanguard's way, DragonStar's: one token for playing it through, one for winning (and being there at the end)."""

    def monster_challenge_earned(self, result):
        return int(result.played_through) + int(result.won and not result.absent)


async def set_hp(db, fight_id, party, hp, strength=None):
    """Sets every fighter of $party to $hp hit points (and, if given, that Strength), before any round has played."""
    record = await db.get(FightRecord, fight_id)
    fight = hydrate(record.initial_state)
    for address in fight.addresses():
        if address[0] == party:
            fighter = fight.get(address)
            fighter.base["HP"] = fighter.current["HP"] = hp
            if strength is not None:
                fighter.base["Strength"] = fighter.current["Strength"] = strength
    record.initial_state = dehydrate(fight)
    await db.flush()
    return record


async def finished(db, fight_id, rules):
    """The fight, after one round in which each side attacks the other: returns the record and the result."""
    record = await db.get(FightRecord, fight_id)
    return record


async def one_round(db, mike, ann, fight_id, rules, volunteer_commands=True):
    record = await db.get(FightRecord, fight_id)
    await live.submit_command(db, record, mike, HERO, Command.ATTACK_LEFT, 0, FOE, rules)
    if volunteer_commands:
        await live.submit_command(db, record, ann, FOE, Command.ATTACK_LEFT, 0, HERO, rules)
    return await live.resolve_round(db, record, rules)


async def monsters_win(db, rules, **kw):
    mike, ann, fight_id = await seated(db, **kw)
    await set_hp(db, fight_id, 0, 1)
    await set_hp(db, fight_id, 1, 500)
    return mike, ann, fight_id


async def monsters_lose(db, rules, **kw):
    mike, ann, fight_id = await seated(db, **kw)
    await set_hp(db, fight_id, 0, 500, strength=60)
    await set_hp(db, fight_id, 1, 1)
    return mike, ann, fight_id


# --- tokens ----------------------------------------------------------------------------------------------------------

async def test_the_default_rules_pay_a_monster_player_nothing_and_lock_them_out_of_nothing(db):
    mike, ann, fight_id = await monsters_win(db, Sure())
    result = await one_round(db, mike, ann, fight_id, Sure())
    assert result.over is True
    assert await challenge.balance(db, ann) == 0
    assert (await db.get(MonsterSeat, fight_id)).settled is True and await db.get(MonsterLockout, ann) is None


async def test_a_game_pays_for_playing_it_through_and_for_winning(db):
    mike, ann, fight_id = await monsters_win(db, Pays())
    await one_round(db, mike, ann, fight_id, Pays())
    assert await challenge.balance(db, ann) == 2
    assert await challenge.balance(db, mike) == 0, "the heroes' player is paid by challenge_earned, which pays nothing here"


async def test_losing_the_fight_after_playing_it_through_earns_the_playing_token_only(db):
    mike, ann, fight_id = await monsters_lose(db, Pays())
    result = await one_round(db, mike, ann, fight_id, Pays())
    assert result.over is True
    assert await challenge.balance(db, ann) == 1 and await db.get(MonsterLockout, ann) is None


async def test_a_missed_round_loses_the_playing_token_and_abandoning_a_lost_fight_locks_the_player_out_for_an_hour(db):
    mike, ann, fight_id = await monsters_lose(db, Pays())
    await one_round(db, mike, ann, fight_id, Pays(), volunteer_commands=False)
    assert (await db.get(MonsterSeat, fight_id)).absent is True
    assert await challenge.balance(db, ann) == 0
    lock = await db.get(MonsterLockout, ann)
    assert monsters._aware(lock.until) == wallclock.now() + timedelta(hours=1)
    with pytest.raises(monsters.MonsterError, match="locked out"):
        await monsters.join(db, RULES, ann)


async def test_an_admin_is_never_locked_out(db):
    mike, ann, fight_id = await monsters_lose(db, Pays())
    await challenge.set_admin(db, ann)
    await one_round(db, mike, ann, fight_id, Pays(), volunteer_commands=False)
    assert await db.get(MonsterLockout, ann) is None


async def test_abandoning_a_fight_the_monsters_won_locks_nobody_out_and_earns_no_win_token(db):
    mike, ann, fight_id = await monsters_win(db, Pays())
    await one_round(db, mike, ann, fight_id, Pays(), volunteer_commands=False)
    assert await challenge.balance(db, ann) == 0 and await db.get(MonsterLockout, ann) is None


async def test_coming_back_before_the_end_is_not_abandoning(db):
    class Slow(Pays):
        def monster_absent(self, missed):
            return False

    mike, ann, fight_id = await monsters_lose(db, Slow())
    await set_hp(db, fight_id, 0, 500)
    record = await db.get(FightRecord, fight_id)
    await live.submit_command(db, record, mike, HERO, Command.DEFEND, 0, HERO, Slow())
    await live.resolve_round(db, record, Slow())  # missed, but the game keeps them in control
    await one_round(db, mike, ann, fight_id, Slow())
    assert await db.get(MonsterLockout, ann) is None
    assert await challenge.balance(db, ann) == 0, "a missed round still loses the playing token"


async def test_a_seat_an_admin_took_earns_nothing_and_locks_nobody_out(db):
    mike, _ann, fight_id = await monsters_lose(db, Pays())
    admin = await an_admin(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record, Pays())
    await live.resolve_round(db, record, Pays())  # (Ann missed: absent; the fight goes on)
    await set_hp(db, fight_id, 1, 1)
    await monsters.take_over(db, Pays(), record, admin)
    await set_hp(db, fight_id, 0, 500)
    await one_round(db, mike, admin, fight_id, Pays())
    assert await challenge.balance(db, admin) == 0 and await db.get(MonsterLockout, admin) is None


async def test_the_daily_cap_cuts_what_a_seat_earns(db):
    class Capped(Pays):
        def challenge_daily_cap(self, account_id):
            return 1

    mike, ann, fight_id = await monsters_win(db, Capped())
    await one_round(db, mike, ann, fight_id, Capped())
    assert await challenge.balance(db, ann) == 1


async def test_a_game_may_set_its_own_lockout(db):
    class Strict(Pays):
        def monster_lockout_seconds(self, result):
            return 60 if result.rounds_missed else 0

    mike, ann, fight_id = await monsters_win(db, Strict())
    await one_round(db, mike, ann, fight_id, Strict(), volunteer_commands=False)
    assert monsters._aware((await db.get(MonsterLockout, ann)).until) == wallclock.now() + timedelta(seconds=60)


async def test_settling_twice_pays_once(db):
    mike, ann, fight_id = await monsters_win(db, Pays())
    await one_round(db, mike, ann, fight_id, Pays())
    record = await db.get(FightRecord, fight_id)
    await store.apply_results(db, record, Pays())
    assert await challenge.balance(db, ann) == 2


async def test_a_new_lockout_never_shortens_a_longer_one(db):
    mike, ann, fight_id = await monsters_lose(db, Pays())
    db.add(MonsterLockout(account_id=ann, until=wallclock.now() + timedelta(days=1)))
    await db.flush()
    await one_round(db, mike, ann, fight_id, Pays(), volunteer_commands=False)
    assert monsters._aware((await db.get(MonsterLockout, ann)).until) == wallclock.now() + timedelta(days=1)


# --- the heroes' bonus ---------------------------------------------------------------------------------------------

async def xp_events(db, fight_id, address=HERO):
    record = await db.get(FightRecord, fight_id)
    out = []
    for action in await store.actions(db, record):
        for raw in action.events:
            each = Event.from_list(raw)
            if each.type is EventType.XP_EARNED and tuple(each.data[:3]) == address:
                out.append(each.data[3])
    return out


class Generous(Sure):
    monster_bonus_cap = 1.0


async def test_beating_a_players_monsters_pays_the_heroes_a_capped_bonus(db):
    mike, ann, fight_id = await monsters_lose(db, Sure())
    await one_round(db, mike, ann, fight_id, Sure())
    paid = await xp_events(db, fight_id)
    base = paid[0]
    assert base > 0
    bonus = math.floor(base * 0.15)
    assert paid == ([base, bonus] if bonus else [base]), "the DragonStar cap is 15 percent of what was earned"


async def test_the_bonus_is_the_overkill_share_up_to_the_cap(db):
    mike, ann, fight_id = await monsters_lose(db, Generous())
    await one_round(db, mike, ann, fight_id, Generous())
    paid = await xp_events(db, fight_id)
    assert len(paid) == 2 and 0 < paid[1] <= paid[0], "the monsters had 1 HP, so the overkill share is the whole cap"


async def test_a_fight_with_no_player_commanding_the_monsters_pays_no_bonus(db):
    from .test_monster_play import fight

    mike, _team, record = await fight(db, monster_play=False, rules=Sure())
    mike_id, fight_id = mike.id, record.id
    await set_hp(db, fight_id, 0, 500)
    await set_hp(db, fight_id, 1, 1)
    record = await db.get(FightRecord, fight_id)
    await live.submit_command(db, record, mike_id, HERO, Command.ATTACK_LEFT, 0, FOE, Generous())
    await live.resolve_round(db, record, Generous())
    assert len(await xp_events(db, fight_id)) == 1


async def test_a_player_who_left_the_monsters_to_the_ai_still_counts_as_having_commanded_them(db):
    mike, ann, fight_id = await monsters_lose(db, Generous())
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record, Generous())
    await live.resolve_round(db, record, Generous())  # Ann missed it and is absent: the AI plays on
    await set_hp(db, fight_id, 1, 1)
    await one_round(db, mike, ann, fight_id, Generous(), volunteer_commands=False)
    assert len(await xp_events(db, fight_id)) == 2


async def test_the_game_may_turn_the_bonus_off(db):
    class NoBonus(Sure):
        monster_bonus_cap = 0.0

    mike, ann, fight_id = await monsters_lose(db, NoBonus())
    await one_round(db, mike, ann, fight_id, NoBonus())
    assert len(await xp_events(db, fight_id)) == 1


async def test_the_monsters_winning_pays_the_heroes_no_bonus(db):
    mike, ann, fight_id = await monsters_win(db, Generous())
    await one_round(db, mike, ann, fight_id, Generous())
    assert await xp_events(db, fight_id) == []
