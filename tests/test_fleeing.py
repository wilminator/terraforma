"""Fleeing: a fighter tries to get away, a party leaves together (or one fighter alone, by the game's rule), the fled still earn
what they did and count as having survived, a fight can forbid it, and the eject ability forces it. The engine's pure parts first,
then the live fight on every database."""

import json

import pytest
from sqlalchemy import select

from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import live, store
from terraforma.fights.combatant import Command
from terraforma.fights.events import Event, EventType
from terraforma.fights.experience import Debt, Fighter, Party, process_experience
from terraforma.fights.fight import build_fight
from terraforma.fights.models import FightParticipant
from terraforma.fights.replay import apply_events
from terraforma.fights.resolve import do_combat
from terraforma.fights.rewards import settle
from terraforma.fights.rules import Rules
from terraforma.fights.specs import EffectSpec
from terraforma.fights import specs
from terraforma.fights.state import dehydrate, hydrate
from terraforma.fights.store import command_record
from terraforma.models import Map, World
from terraforma.world.start import ensure_start

from .test_fight_rules import Scripted, spell, types
from .test_fight_rules import fighter as plain_fighter
from .test_live_fights import RULES as LIVE_RULES
from .test_live_fights import a_team

pytestmark = pytest.mark.anyio

RULES = Rules()


def fighter(name, charid=None, gold=0, **stats):
    """A fight-rules fighter that is a hero when it has a $charid (and carries $gold when it is a monster)."""
    made = plain_fighter(name, **stats)
    made.charid, made.gold = charid, gold
    return made


class Alone(Rules):
    """Vanguard Tavern's way: only the fighter that fled leaves (back in its party's place after the fight)."""

    def flee_party(self, fight, address):
        return [address]


def crew(count=2, foe_speed=10, **foe):
    """A party of $count heroes (Speed 10, on teams 7 and 8 when there are two) against one monster."""
    heroes = [fighter(f"hero{number}", charid=number + 1) for number in range(count)]
    monster = fighter("monster", Speed=foe_speed, **foe)
    fight = build_fight({0: {0: heroes}, 1: {0: [monster]}})
    fight.parties[0].teams = {7 + number: [number + 1] for number in range(count)}
    return fight, heroes, monster


def runs(hero):
    hero.command = Command.RUN


# --- the chance -------------------------------------------------------------------------------------------------

def test_the_default_chance_is_the_fighters_speed_against_every_enemys_speed_added_up():
    fight, heroes, _monster = crew(foe_speed=30)
    assert RULES.flee_chance(fight, (0, 0, 0)) == 25.0
    extra = fighter("second", Speed=10)
    fight.parties[1].groups[0].characters[1] = extra
    assert RULES.flee_chance(fight, (0, 0, 0)) == 20.0, "a second enemy adds its speed"
    extra.fled = True
    assert RULES.flee_chance(fight, (0, 0, 0)) == 25.0, "one that left the fight does not"
    assert RULES.flee_chance(fight, (1, 0, 0)) == 100 * 30 / 50, "and the monsters' own chance is worked out the same way"


def test_a_roll_within_the_chance_gets_away():
    rng = Scripted(25, 26)
    assert RULES.flee_succeeds(rng, 25.0) is True and RULES.flee_succeeds(rng, 25.0) is False


# --- a flee that works and one that does not --------------------------------------------------------------------------

def test_a_flee_that_works_takes_the_whole_party_out_and_the_fight_is_over():
    fight, (a, b), monster = crew()
    runs(a)
    b.command = monster.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 1))  # a's speed, then the flee roll
    assert types(events)[:4] == [EventType.TURN, EventType.RUN, EventType.FLED, EventType.FLED]
    assert [each.data for each in events if each.type is EventType.FLED] == [(0, 0, 0, "flee"), (0, 0, 1, "flee")]
    assert a.fled and b.fled and a.alive(RULES) and not a.present(RULES)
    assert fight.over and fight.parties[0].dead(RULES) and not fight.parties[1].dead(RULES)
    assert EventType.FIGHT_OVER in types(events)
    assert not any(each.type is EventType.PARTY_LOST for each in events), "a party that got away lost nothing"


def test_a_flee_that_fails_costs_only_the_turn():
    fight, (a, b), monster = crew()
    runs(a)
    b.command = monster.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 51))
    assert types(events) == [EventType.TURN, EventType.RUN, EventType.FLEE_FAILED]
    assert EventType.FLED not in types(events) and not a.fled and not fight.over


def test_a_fight_that_cannot_be_fled_shows_the_try_and_nothing_comes_of_it():
    fight, (a, b), monster = crew()
    fight.can_flee = False
    runs(a)
    b.command = monster.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100))
    assert types(events)[:2] == [EventType.TURN, EventType.RUN]
    assert EventType.FLED not in types(events) and EventType.FLEE_FAILED not in types(events) and not a.fled


def test_the_game_may_let_only_the_fighter_leave_and_the_rest_play_on():
    fight, (a, b), monster = crew()
    runs(a)
    b.command, monster.command, monster.target = Command.ATTACK_LEFT, Command.ATTACK_LEFT, (0, 0, 0)
    b.target = (1, 0, 0)
    events = do_combat(fight, Alone(), Scripted(100, 100, 100, 1, 100, 1, *[50] * 20))
    assert [each.data for each in events if each.type is EventType.FLED] == [(0, 0, 0, "flee")]
    assert a.fled and not b.fled and not fight.parties[0].dead(RULES)


def test_a_fled_fighter_does_not_act_is_not_hit_and_its_place_is_taken_by_a_neighbour():
    fight, (a, b), monster = crew()
    a.fled = True
    monster.command, monster.target = Command.ATTACK_LEFT, (0, 0, 0)
    a.command = Command.ATTACK_LEFT
    a.target = (1, 0, 0)
    b.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 100, 1, 100))
    hit = [each for each in events if each.type in (EventType.DAMAGE, EventType.MISS)]
    assert hit and all(each.data[:3] == (0, 0, 1) for each in hit), "the attack aimed at the one who left lands on the one who stayed"
    assert not any(each.type is EventType.TURN and each.data == (0, 0, 0) for each in events), "and the one who left takes no turn"
    assert monster.current["HP"] == monster.base["HP"]


def test_the_rules_hear_a_flee_as_events_and_replaying_them_leaves_the_same_fight():
    fight, (a, b), monster = crew()
    runs(a)
    b.command = monster.command = Command.DEFEND
    before = hydrate(json.loads(json.dumps(dehydrate(fight))))  # (the commands are part of what a round starts from)
    events = do_combat(fight, RULES, Scripted(100, 1))
    apply_events(before, RULES, [Event.from_list(each.to_list()) for each in events])
    assert dehydrate(before) == dehydrate(fight)
    assert before.get((0, 0, 1)).fled and before.over


def test_a_fight_state_keeps_who_fled_and_whether_it_can_be_fled_and_a_plain_fight_reads_as_it_always_did():
    fight, (a, _b), _monster = crew()
    plain = dehydrate(fight)
    assert "can_flee" not in plain and not any("fled" in each for party in plain["parties"] for group in party["groups"] for each in group["characters"])
    a.fled, fight.can_flee = True, False
    again = hydrate(json.loads(json.dumps(dehydrate(fight))))
    assert again.get((0, 0, 0)).fled and not again.get((0, 0, 1)).fled and again.can_flee is False
    assert hydrate(plain).can_flee is True


# --- what a fight pays out ----------------------------------------------------------------------------------------------

def test_a_fled_fighter_keeps_what_it_earned_and_loses_its_party_and_team_bonuses_that_the_others_still_get():
    def side(fled):
        return [
            Party(0, [Fighter((0, 0, 0), charid=1, fled=fled), Fighter((0, 0, 1), charid=2)], False, frozenset({0}), frozenset({1}), {7: [1, 2]}),
            Party(1, [Fighter((1, 0, 0), [Debt((0, 0, 0), 1.0, 100)])], True, frozenset({1}), frozenset({0})),
        ]

    stayed = process_experience(side(False)).earned
    left = process_experience(side(True)).earned
    assert stayed == {(0, 0, 0): 50 + 10 + 15, (0, 0, 1): 10 + 15}
    assert left == {(0, 0, 0): 50, (0, 0, 1): 10 + 15}, "its own half only; the pools are worked out as if it had stayed"


def finished_fight(*, hero_down=True, hero_fled=False):
    """Two teams (7: hero 1, 8: hero 2) against a monster that is down and worth 10 gold."""
    heroes = [fighter("one", charid=1), fighter("two", charid=2)]
    monster = fighter("monster", HP=1, gold=10)
    monster.current["HP"] = 0
    fight = build_fight({0: {0: heroes}, 1: {0: [monster]}})
    fight.parties[0].teams = {7: [1], 8: [2]}
    heroes[1].current["HP"] = 0 if hero_down else 20
    heroes[0].fled = hero_fled
    return fight, heroes


def test_gold_goes_to_the_teams_with_a_hero_alive_and_a_hero_that_fled_counts():
    fight, _ = finished_fight(hero_down=True)
    gold = [each.data for each in settle(RULES, fight, Scripted()) if each.type is EventType.GOLD]
    assert gold == [(0, 7, 10)], "team 8 is all down: no gold, and team 7's share is the lot"
    fight, _ = finished_fight(hero_down=False)
    gold = [each.data for each in settle(RULES, fight, Scripted()) if each.type is EventType.GOLD]
    assert gold == [(0, 7, 5), (0, 8, 5)]
    fight, _ = finished_fight(hero_down=True, hero_fled=True)
    gold = [each.data for each in settle(RULES, fight, Scripted()) if each.type is EventType.GOLD]
    assert gold == [(0, 7, 10)], "the one that fled is alive: its team is paid"


def test_a_party_that_stayed_to_the_end_all_down_is_lost_and_the_game_is_told():
    told = []

    class Home(Rules):
        def party_lost(self, fight, party):
            told.append(party)
            return []

    fight, (one, two) = finished_fight(hero_down=True, hero_fled=True)
    events = settle(Home(), fight, Scripted())
    assert [each.data for each in events if each.type is EventType.PARTY_LOST] == [(0,)] and told == [0], "one fled, the one who stayed is down"
    fight, (one, two) = finished_fight(hero_down=False)
    assert not any(each.type is EventType.PARTY_LOST for each in settle(Home(), fight, Scripted())), "someone is standing"
    fight, (one, two) = finished_fight(hero_down=True)
    one.current["HP"] = 0
    assert [each.data for each in settle(Home(), fight, Scripted()) if each.type is EventType.PARTY_LOST] == [(0,)]
    fight, (one, two) = finished_fight(hero_down=True)
    one.fled = two.fled = True
    assert not any(each.type is EventType.PARTY_LOST for each in settle(Home(), fight, Scripted())), "all of them got away"


def test_a_team_with_everyone_down_gets_no_drops_but_one_that_fled_does():
    from terraforma.fights.drops import DropEntry, DropTable
    from terraforma.fights.specs import ItemSpec

    herb = ItemSpec("herb", "Herb")
    table = DropTable("herbs", entries=(DropEntry(herb, 10000, share="each_member"),))

    def received(hero_down, hero_fled):
        fight, heroes = finished_fight(hero_down=hero_down, hero_fled=hero_fled)
        fight.get((1, 0, 0)).drops = ("herbs",)
        fight.drop_tables = {"herbs": table}
        return sorted(each.data[:3] for each in settle(RULES, fight, Scripted(*[1] * 10)) if each.type is EventType.DROP)

    assert received(True, True) == [(0, 0, 0)], "team 8 is down; the fled one on team 7 is alive"
    assert received(False, False) == [(0, 0, 0), (0, 0, 1)]


# --- the eject ability ------------------------------------------------------------------------------------------------------

def test_an_ejects_chance_is_its_rating_moved_by_power_against_resistance_between_half_and_double():
    assert RULES.eject_chance(40, 10, 10) == 40
    assert RULES.eject_chance(40, 30, 10) == 80 and RULES.eject_chance(40, 1000, 1) == 80, "twice at most"
    assert RULES.eject_chance(40, 10, 40) == 20 and RULES.eject_chance(40, 1, 1000) == 20, "half at least"
    assert RULES.eject_chance(80, 1000, 1) == 100, "and never over 100"


def ejection(base=100, friendly=False, target=(1, 0, 0), foe_party=True):
    fight, (a, b), monster = crew()
    a.abilities = [spell(EffectSpec(specs.EJECT, specs.INDIVIDUAL, base=base, friendly=friendly), cost=0)]
    a.command, a.using, a.target = Command.SPELL, 0, target
    b.command = monster.command = Command.DEFEND
    return fight, a, b, monster


def test_an_eject_that_works_throws_the_target_out_like_a_flee_and_its_whole_party_goes():
    fight, a, b, monster = ejection()
    events = do_combat(fight, RULES, Scripted(100, 100, 1))  # a's speed and focus (it casts), then the eject roll
    assert monster.fled and (0, 0, 0, "eject") not in [each.data for each in events if each.type is EventType.FLED]
    assert [each.data for each in events if each.type is EventType.FLED] == [(1, 0, 0, "eject")]
    assert fight.over and not monster.current["HP"] == 0, "the monster is not dead, it left: no gold, no drops"


def test_an_eject_that_does_not_work_says_so():
    fight, a, b, monster = ejection(base=10)
    events = do_combat(fight, RULES, Scripted(100, 100, 50))
    assert EventType.NO_EFFECT in types(events) and not monster.fled and not fight.over


def test_an_eject_will_not_touch_the_users_own_side_unless_it_is_friendly():
    fight, a, b, _monster = ejection(target=(0, 0, 1))
    events = do_combat(fight, RULES, Scripted(100, 100, 1))
    assert EventType.NO_EFFECT in types(events) and not b.fled
    fight, a, b, _monster = ejection(target=(0, 0, 1), friendly=True)
    do_combat(fight, Alone(), Scripted(100, 100, 1))
    assert b.fled and not a.fled, "a friendly eject lets a party send one of its own out of a fight"


def test_an_eject_does_nothing_where_fleeing_is_not_allowed():
    fight, a, b, monster = ejection()
    fight.can_flee = False
    events = do_combat(fight, RULES, Scripted(100, 100, 1))
    assert EventType.NO_EFFECT in types(events) and not monster.fled


# --- the seed ---------------------------------------------------------------------------------------------------------------------

def test_the_seed_knows_the_eject_effect_and_where_a_zone_can_be_fled():
    ok = check_seed({"abilities": [{"key": "banish", "name": "Banish", "kind": "spell", "effect": {"effect": "eject", "base": 60, "friendly": True}}]})
    assert ok["abilities"][0].effect.effect == "eject" and ok["abilities"][0].effect.friendly is True
    assert EffectSpec.from_dict({"effect": "eject", "base": 60, "friendly": True}).friendly is True
    with pytest.raises(ContentError, match="friendly"):
        check_seed({"abilities": [{"key": "a", "name": "A", "kind": "skill", "effect": {"effect": "hurt", "friendly": True}}]})
    with pytest.raises(ContentError, match="out of 100"):
        check_seed({"abilities": [{"key": "a", "name": "A", "kind": "skill", "effect": {"effect": "eject", "base": 101}}]})
    zone = check_seed({"maps": [{"key": "m", "name": "M", "tileset": [{}], "tiles": [[0]], "zones": [{"name": "boss", "can_flee": False}, {"name": "plain"}]}]})["maps"][0].zones
    assert [each.can_flee for each in zone] == [False, True]


# --- the live fight ------------------------------------------------------------------------------------------------------------------

class Certain(Rules):
    """A flee that always works."""

    def flee_chance(self, fight, address):
        return 100.0


async def test_a_fight_can_forbid_fleeing_and_a_zone_can_say_so_for_every_fight_in_it(db):
    hero, mike, team = await a_team(db)
    hub = await db.get(Map, hero.map_id)
    hub.zones = [{"name": "boss room", "can_flee": False}]
    record = await live.start_team_fight(db, team, ["rat"], LIVE_RULES)
    fight, _ = await store.load_state(db, record, LIVE_RULES)
    assert fight.can_flee is False
    with pytest.raises(live.Refused, match="no running"):
        await live.submit_command(db, record, mike.id, (0, 0, 0), Command.RUN, 0, (1, 0, 0), LIVE_RULES)
    assert (await live.view(db, record, LIVE_RULES, mike.id))["fighters"][0]["fled"] is False


async def test_a_scripted_fight_may_forbid_it_whatever_the_zone_says(db):
    _hero, _mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], LIVE_RULES, can_flee=False)
    assert (await store.load_state(db, record, LIVE_RULES))[0].can_flee is False


async def test_a_hero_that_flees_is_free_of_the_fight_its_vitals_are_saved_and_it_cannot_command_again(db):
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["ogre"], Certain())
    assert await live.submit_command(db, record, mike.id, (0, 0, 0), Command.RUN, 0, (1, 0, 0), Certain()) == 1
    result = await live.resolve_round(db, record, Certain())
    assert result.over, "the whole party left: the monsters are all that is left"
    row = await db.scalar(select(FightParticipant).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id == hero.id))
    assert row.fled is True
    fight, _ = await store.load_state(db, record, Certain())
    assert fight.get((0, 0, 0)).fled
    view = await live.view(db, record, Certain(), mike.id)
    assert [each["fled"] for each in view["fighters"]] == [True, False]
    with pytest.raises(live.Refused, match="over"):
        await live.submit_command(db, record, mike.id, (0, 0, 0), Command.DEFEND, 0, (1, 0, 0), Certain())


async def test_a_fled_hero_is_not_in_a_fight_for_walking_or_starting_another(db):
    hero, _mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["ogre"], Certain())
    row = await db.scalar(select(FightParticipant).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id == hero.id))
    with pytest.raises(live.Refused, match="already in a fight"):
        await live.start_team_fight(db, team, ["rat"], Certain())
    row.fled = True
    await db.flush()
    assert (await live.start_team_fight(db, team, ["rat"], Certain())).id != record.id


async def fled_from_a_three_sided_fight(db):
    """A hero fights one monster (it kills it) and then flees from the other, which wins the fight."""
    hub = await ensure_start(db)
    (await db.get(World, hub.world_id)).seed = 20261006
    from .test_fight_store import a_hero

    hero = await a_hero(db)
    brave = fighter("Aria", Accuracy=10000, Strength=500, Speed=50, charid=hero.id)
    weak = fighter("Weak", HP=1, Dodge=1)
    strong = fighter("Strong", HP=500, Strength=500, Accuracy=10000, Speed=1, gold=0)
    fight = build_fight({0: {0: [brave]}, 1: {0: [weak]}, 2: {0: [strong]}})
    fight.parties[0].teams = {1: [hero.id]}
    record = await store.create_fight(db, hub, fight)
    hero.xp, hero.vitals = 0, None
    return hero, record


async def test_a_hero_that_fled_from_a_fight_that_goes_on_gets_its_share_added_once_and_keeps_what_it_did_since(db):
    hero, record = await fled_from_a_three_sided_fight(db)
    rules = Certain()
    await store.play_round(db, record, [command_record((0, 0, 0), Command.ATTACK_LEFT, 0, (1, 0, 0))], rules)
    await store.play_round(db, record, [command_record((0, 0, 0), Command.RUN, 0, (2, 0, 0))], rules)
    row = await db.scalar(select(FightParticipant).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id == hero.id))
    assert row.fled is True and row.settled is False and hero.vitals is not None, "free of the fight, and what it stood at saved"
    hero.xp = 500  # it carried on elsewhere while the fight ran
    hero.vitals = {"HP": 7, "MP": 1}
    fight, _ = await store.load_state(db, record, rules)
    assert fight.over, "its party is gone and the last is alone"
    await store.apply_results(db, record, rules)
    earned = fight.get((0, 0, 0)).exp
    assert earned > 0 and hero.xp == 500 + earned, "added to what it has, not written over it"
    assert hero.vitals == {"HP": 7, "MP": 1}, "what it stood at when it left is not written over what it stands at now"
    await store.apply_results(db, record, rules)
    assert hero.xp == 500 + earned, "once"
    await db.refresh(row)
    assert row.settled is True


async def test_a_hero_that_stayed_is_written_as_the_fight_left_it(db):
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], LIVE_RULES)
    await live.submit_command(db, record, mike.id, (0, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0), LIVE_RULES)
    hero.xp = 999
    result = await live.resolve_round(db, record, LIVE_RULES)
    fight, _ = await store.load_state(db, record, LIVE_RULES)
    if result.over:
        assert hero.xp == fight.get((0, 0, 0)).exp, "as it always was: the fight's number replaces the hero's"
