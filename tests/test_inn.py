"""Resting at an inn: the engine's default for the Yes of an inn tag (each team pays its own, the leader may treat the party),
through a real conversation, on every database."""

from functools import partial

import pytest
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.economy import TeamGold
from terraforma.fights.rules import Rules
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Team
from terraforma.npcs import inn as inns
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.reach.hooks import Reach
from terraforma.npcs.inn import Inn
from terraforma.parties import service as parties

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
NPCS, ECONOMY, RULES = Npcs(), TeamGold(), Rules()
REACH = Reach()
INN = "Welcome.`inn,10,no`A bed is 10 gold. Rest?`ack`Sleep well.`jump,end``label,no`Another time."


class Treat(Inn):
    """The leader pays for the whole party."""

    async def leader_pays(self, session, hero, price):
        return True


class Party:
    """Mike's Vanguard (Aria) and Rearguard (Bram), and Zed's Scouts (Cleo), in one party in that order: Mike leads."""


async def a_party(db):
    await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    out = Party()
    out.teams, out.heroes = [], []
    for owner, team_name, hero_name in ((mike, "Vanguard", "Aria"), (mike, "Rearguard", "Bram"), (zed, "Scouts", "Cleo")):
        team = await heroes.create_team(db, owner, team_name)
        hero = await heroes.create_hero(db, owner, hero_name, "fighter")
        await heroes.add_to_team(db, owner, team.id, hero.id)
        hero.vitals = {"HP": 1, "MP": 0}  # hurt
        out.teams.append(team.id)
        out.heroes.append(hero)
    out.party = await parties.create_party(db, out.teams[0], 20)
    await parties.join_party(db, out.party.id, out.teams[1], 20)
    await parties.join_party(db, out.party.id, out.teams[2], 20)
    await db.flush()
    return out


async def give_gold(db, team_id, gold):
    team = await db.get(Team, team_id)
    team.gold = gold
    await db.flush()


async def gold(db, team_id):
    team = await db.get(Team, team_id)
    await db.refresh(team, ["gold"])
    return team.gold


async def say_yes(db, hero, inn=None, dialog=INN):
    """The hero talks to the innkeeper and answers Yes; returns the next frame."""
    rest = partial(inns.rest, db, inn or Inn(), RULES, ECONOMY)
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, dialog)
    asked = await service.talk(db, NPCS, REACH, hero, npc.id, rest)
    assert asked["prompt"]["kind"] == "inn"
    return await service.answer(db, NPCS, REACH, hero, 0, rest)


def rested(hero):
    return hero.vitals is None


async def test_the_talking_heros_team_pays_and_rests_and_the_talk_goes_on(db):
    p = await a_party(db)
    await give_gold(db, p.teams[0], 25)
    frame = await say_yes(db, p.heroes[0])
    assert frame["prompt"] == {"type": "ack", "accepts": {"choice": [], "next": True, "cancel": False}}
    assert frame["events"] == [], "the ack comes first"
    assert (await service.answer(db, NPCS, REACH, p.heroes[0], None))["events"][0]["text"] == "Sleep well."
    assert rested(p.heroes[0]) and not rested(p.heroes[1]) and not rested(p.heroes[2]), "only the team that paid"
    assert await gold(db, p.teams[0]) == 15 and await gold(db, p.teams[1]) == 0


async def test_a_team_that_cannot_pay_stays_in_the_conversation_and_rests_nothing(db):
    p = await a_party(db)
    await give_gold(db, p.teams[0], 9)
    with pytest.raises(service.NpcError, match="not enough gold"):
        await say_yes(db, p.heroes[0])
    assert not rested(p.heroes[0]) and await gold(db, p.teams[0]) == 9
    assert (await service.current(db, p.heroes[0]))["talking"], "the answer was refused, not the conversation"


async def test_a_hero_on_no_team_rests_alone_and_pays_their_own_gold(db):
    await load_content(db, SEED)
    owner = await create_account(db, "Loner", PASSWORD, email="loner@example.com", confirmed=True)
    hero = await heroes.create_hero(db, owner, "Dax", "fighter")
    hero.vitals, hero.gold = {"HP": 1, "MP": 0}, 12
    await db.flush()
    await say_yes(db, hero)
    assert rested(hero) and await ECONOMY.balance(db, hero) == 2


async def test_the_leader_may_pay_for_every_team_when_the_game_lets_them(db):
    p = await a_party(db)
    await give_gold(db, p.teams[0], 30)
    await say_yes(db, p.heroes[0], Treat())
    assert all(rested(hero) for hero in p.heroes)
    assert await gold(db, p.teams[0]) == 0, "three teams at 10 each, all from the leader's purse"


async def test_the_leader_pays_for_as_many_teams_as_they_can_afford_in_formation_order(db):
    p = await a_party(db)
    await give_gold(db, p.teams[0], 25)
    await say_yes(db, p.heroes[0], Treat())
    assert rested(p.heroes[0]) and rested(p.heroes[1]) and not rested(p.heroes[2]), "the last team is the one left out"
    assert await gold(db, p.teams[0]) == 5


async def test_only_the_leading_team_can_treat_the_party(db):
    p = await a_party(db)
    await give_gold(db, p.teams[2], 30)
    await say_yes(db, p.heroes[2], Treat())  # Cleo's Scouts are Zed's: not the leading team
    assert rested(p.heroes[2]) and not rested(p.heroes[0]) and not rested(p.heroes[1])
    assert await gold(db, p.teams[2]) == 20


async def test_a_game_that_handles_the_yes_itself_is_left_alone(db):
    p = await a_party(db)

    class Closed(Npcs):
        async def tag(self, session, hero, command, parts):
            return parts[1] if command == "inn" else None  # "the inn is full": go to the No label

    npc = await service.place_npc(db, "keeper", "Keeper", p.heroes[0].map_id, p.heroes[0].x, p.heroes[0].y + 1, INN)
    rest = partial(inns.rest, db, Inn(), RULES, ECONOMY)
    await service.talk(db, Closed(), REACH, p.heroes[0], npc.id, rest)
    frame = await service.answer(db, Closed(), REACH, p.heroes[0], 0, rest)
    assert frame["events"][-1]["text"] == "Another time." and not rested(p.heroes[0])
