"""A team's points and rank with a guild, the party gate, and the dialog tags that use them, on every database."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from terraforma.fights.rules import Rules
from terraforma.guild import ranks
from terraforma.guild.models import GuildStanding
from terraforma.heroes import service as heroes
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.npcs.script import ScriptError, parse
from terraforma.reach.hooks import Reach

from .test_npc_state import a_party, npc_says

pytestmark = pytest.mark.anyio

RULES = Rules()


async def says(db, hero, dialog, rules=RULES):
    """What an NPC says to the hero, whole, with (or without) the game's rules to measure a party."""
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, dialog)
    frame = await service.talk(db, Npcs(), Reach(), hero, npc.id, None, None, rules)
    assert frame["ended"], frame
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


def check(command):
    return f"`{command}`pass`jump,end``label,no`no"


# --- points and rank -----------------------------------------------------------------------------------------------

async def test_a_team_has_no_points_and_the_lowest_rank_until_it_earns_some(db):
    p = await a_party(db)
    assert await ranks.standing(db, p.teams[0], "vanguard") == (0, 0)
    assert await db.scalar(select(func.count()).select_from(GuildStanding)) == 0, "looking makes no row"


async def test_points_add_up_and_a_rank_is_only_what_the_dialog_sets(db):
    p = await a_party(db)
    assert await ranks.add_points(db, p.teams[0], "vanguard", 30) == 30
    assert await ranks.add_points(db, p.teams[0], "vanguard", 20) == 50
    assert await ranks.standing(db, p.teams[0], "vanguard") == (50, 0), "points alone never promote"
    await ranks.set_rank(db, p.teams[0], "vanguard", 3)
    assert await ranks.standing(db, p.teams[0], "vanguard") == (50, 3)
    await ranks.set_rank(db, p.teams[0], "vanguard", 1)
    assert await ranks.standing(db, p.teams[0], "vanguard") == (50, 1), "a game may demote"


async def test_standing_belongs_to_the_team_and_the_guild(db):
    p = await a_party(db)
    await ranks.add_points(db, p.teams[0], "vanguard", 10)
    await ranks.set_rank(db, p.teams[0], "vanguard", 2)
    assert await ranks.standing(db, p.teams[1], "vanguard") == (0, 0)
    assert await ranks.standing(db, p.teams[0], "crown") == (0, 0)


async def test_bad_points_ranks_and_guilds_are_refused(db):
    p = await a_party(db)
    for call in (ranks.add_points(db, p.teams[0], "vanguard", 0), ranks.add_points(db, p.teams[0], "", 1), ranks.set_rank(db, p.teams[0], "vanguard", -1), ranks.set_rank(db, p.teams[0], "vanguard", 100)):
        with pytest.raises(ranks.RankError):
            await call


async def test_a_team_that_is_deleted_takes_its_standing_with_it(db):
    p = await a_party(db)
    lone = await heroes.create_team(db, p.mike, "Lone")
    await ranks.add_points(db, p.teams[0], "vanguard", 10)
    await ranks.add_points(db, lone.id, "vanguard", 10)
    await ranks.set_rank(db, lone.id, "crown", 1)
    await heroes.delete_team(db, p.mike, lone.id)
    assert await db.scalar(select(func.count()).select_from(GuildStanding)) == 1, "only the other team's standing is left"
    assert await ranks.standing(db, p.teams[0], "vanguard") == (10, 0)


# --- the gate ------------------------------------------------------------------------------------------------------

def test_the_gate_asks_the_average_the_lowest_and_the_best_rank():
    d_average_with_a_b = ranks.Gate(average=Decimal(1), highest=3)  # (E is 0, D 1, C 2, B 3)
    assert ranks.refusal(d_average_with_a_b, [3, 1, 0], 0) is None, "average 1.33 and a B"
    assert ranks.refusal(d_average_with_a_b, [2, 1, 0], 0), "no B"
    assert ranks.refusal(d_average_with_a_b, [3, 0, 0], 0) is None, "exactly an average of 1"
    assert ranks.refusal(d_average_with_a_b, [3, 0, 0, 0], 0), "an average of less than 1"
    assert ranks.refusal(ranks.Gate(average=Decimal("1.5")), [2, 1], 0) is None
    assert ranks.refusal(ranks.Gate(average=Decimal("1.5")), [2, 0], 0)
    assert ranks.refusal(ranks.Gate(lowest=1), [3, 1, 1], 0) is None
    assert ranks.refusal(ranks.Gate(lowest=1), [3, 1, 0], 0)


def test_the_gate_asks_the_partys_strength_within_a_window():
    gate = ranks.Gate(pxp_min=100, pxp_max=200)
    assert ranks.refusal(gate, [0], 99) and ranks.refusal(gate, [0], 201)
    assert ranks.refusal(gate, [0], 100) is None and ranks.refusal(gate, [0], 200) is None
    assert ranks.refusal(ranks.Gate(pxp_min=100), [0], 10**9) is None, "no ceiling when it is 0"


def test_a_gate_with_no_party_passes_nothing():
    assert ranks.refusal(ranks.Gate(), [], 0)


# --- the dialog tags -----------------------------------------------------------------------------------------------

async def test_the_tags_award_points_and_set_the_rank_of_the_talking_heros_team(db):
    p = await a_party(db)
    assert await says(db, p.heroes[0], "`add_guild_points,vanguard,40``set_guild_rank,vanguard,2`done") == "done"
    assert await ranks.standing(db, p.teams[0], "vanguard") == (40, 2)
    assert await ranks.standing(db, p.teams[1], "vanguard") == (0, 0), "only the talker's team"


async def test_guild_points_and_guild_rank_compare_by_scope(db):
    p = await a_party(db)  # the party's teams in order: Vanguard (leads), Rearguard, Scouts
    for team_id, points, rank in ((p.teams[0], 40, 3), (p.teams[1], 10, 1), (p.teams[2], 0, 0)):
        if points:
            await ranks.add_points(db, team_id, "vanguard", points)
        await ranks.set_rank(db, team_id, "vanguard", rank)
    hero = p.heroes[1]
    assert await says(db, hero, check("guild_rank,vanguard,ge,3,any,no")) == "pass", "any team is a B"
    assert await says(db, hero, check("guild_rank,vanguard,ge,3,lead,no")) == "pass", "the leading team is"
    assert await says(db, hero, check("guild_rank,vanguard,ge,3,each,no")) == "no"
    assert await says(db, hero, check("guild_rank,vanguard,ge,0,each,no")) == "pass"
    assert await says(db, hero, check("guild_rank,crown,ge,1,any,no")) == "no", "another guild"
    assert await says(db, hero, check("guild_points,vanguard,ge,40,lead,no")) == "pass"
    assert await says(db, hero, check("guild_points,vanguard,gt,40,any,no")) == "no"
    assert await says(db, hero, check("guild_points,vanguard,lt,1,any,no")) == "pass", "the Scouts have none"


async def test_party_gate_checks_ranks_for_the_whole_party(db):
    p = await a_party(db)
    for team_id, rank in zip(p.teams, (3, 1, 0)):
        await ranks.set_rank(db, team_id, "vanguard", rank)
    hero = p.heroes[2]
    assert await says(db, hero, check("party_gate,vanguard,1,0,3,0,0,no")) == "pass", "a D on average with a B"
    assert await says(db, hero, check("party_gate,vanguard,1.5,0,3,0,0,no")) == "no", "average 1.33"
    assert await says(db, hero, check("party_gate,vanguard,0,1,0,0,0,no")) == "no", "the Scouts are below the lowest"
    assert await says(db, hero, check("party_gate,vanguard,0,0,4,0,0,no")) == "no", "nobody is an A"
    assert await says(db, hero, check("party_gate,crown,0,0,1,0,0,no")) == "no", "no standing with another guild"


async def test_party_gate_measures_the_partys_strength_with_the_rules(db):
    p = await a_party(db)
    strength = await ranks.party_pxp(db, RULES, p.teams)
    assert strength > 0
    hero = p.heroes[0]
    assert await says(db, hero, check(f"party_gate,vanguard,0,0,0,{strength},{strength},no")) == "pass"
    assert await says(db, hero, check(f"party_gate,vanguard,0,0,0,{strength + 1},0,no")) == "no", "too weak"
    assert await says(db, hero, check(f"party_gate,vanguard,0,0,0,0,{strength - 1},no")) == "no", "too strong"


async def test_a_gate_on_strength_needs_the_rules_but_a_gate_on_ranks_does_not(db):
    p = await a_party(db)
    assert await says(db, p.heroes[0], check("party_gate,vanguard,0,0,0,0,0,no"), rules=None) == "pass"
    with pytest.raises(service.NpcError, match="no rules"):
        await says(db, p.heroes[0], check("party_gate,vanguard,0,0,0,1,0,no"), rules=None)


async def test_a_hero_on_no_team_has_no_standing(db):
    p = await a_party(db)
    loner = await heroes.create_hero(db, p.mike, "Dax", "fighter")
    with pytest.raises(service.NpcError, match="on no team"):
        await npc_says(db, loner, "`add_guild_points,vanguard,5`")
    assert await says(db, loner, check("party_gate,vanguard,0,0,0,0,0,no")) == "no", "no party"


def test_the_tags_are_checked_when_the_text_is_read():
    for bad in ("add_guild_points,vanguard,0", "add_guild_points,vanguard", "add_guild_points,,5", "set_guild_rank,vanguard,100", "set_guild_rank,vanguard,-1",
                "guild_rank,vanguard,ge,1,all,no", "guild_points,vanguard,near,1,any,no", "guild_rank,vanguard,ge,x,any,no",
                "party_gate,vanguard,1,0,0,0,0", "party_gate,vanguard,x,0,0,0,0,no", "party_gate,vanguard,1.234,0,0,0,0,no", "party_gate,vanguard,1,0,0,0,-1,no"):
        with pytest.raises(ScriptError):
            parse(f"`{bad}`text`label,no`")
    parse("`party_gate,vanguard,1.5,0,3,100,0,no`text`label,no`")
    parse("`guild_rank,vanguard,ge,2,each,no`text`label,no`")


# --- standing with other guilds ------------------------------------------------------------------------------------

async def test_meets_any_asks_each_guild_its_own_rank(db):
    p = await a_party(db)
    team = p.teams[0]
    await ranks.set_rank(db, team, "aldermere", 3)
    await ranks.set_rank(db, team, "ironhand", 2)
    assert await ranks.meets_any(db, team, [("aldermere", 3)])
    assert not await ranks.meets_any(db, team, [("aldermere", 4)])
    assert await ranks.meets_any(db, team, [("sunvale", 3), ("aldermere", 3)]), "one guild is enough"
    assert not await ranks.meets_any(db, team, [("sunvale", 3), ("ironhand", 3)]), "a guild the team never joined is rank 0"
    assert await ranks.meets_any(db, team, [("sunvale", 0)]), "rank 0 is what everyone has"
    assert not await ranks.meets_any(db, team, []), "no requirements pass no one"


async def test_guild_rank_any_lets_in_a_team_that_meets_one_guild(db):
    p = await a_party(db)  # Vanguard (leads), Rearguard, Scouts
    await ranks.set_rank(db, p.teams[0], "aldermere", 3)
    await ranks.set_rank(db, p.teams[1], "ironhand", 4)
    hero = p.heroes[1]
    door = "guild_rank_any,{scope},no,aldermere,3,sunvale,3,ironhand,4"
    assert await says(db, hero, check(door.format(scope="any"))) == "pass"
    assert await says(db, hero, check(door.format(scope="lead"))) == "pass", "the leading team has the kingdom's B"
    assert await says(db, hero, check(door.format(scope="each"))) == "no", "the Scouts meet none"
    await ranks.set_rank(db, p.teams[2], "sunvale", 5)
    assert await says(db, hero, check(door.format(scope="each"))) == "pass", "every team meets one, not the same one"
    assert await says(db, hero, check("guild_rank_any,any,no,aldermere,4,sunvale,6")) == "no", "ranks too low"
    assert await says(db, hero, check("guild_rank_any,lead,no,sunvale,5")) == "no", "the lead has nothing at Sunvale"


async def test_guild_rank_any_is_refused_when_the_text_is_read_if_it_is_malformed():
    for bad in ("guild_rank_any,any,no", "guild_rank_any,any,no,vanguard", "guild_rank_any,all,no,vanguard,1", "guild_rank_any,any,no,vanguard,x",
                "guild_rank_any,any,no,vanguard,100", "guild_rank_any,any,no,,1", "guild_rank_any,any,no,a,1,b"):
        with pytest.raises(ScriptError):
            parse(f"`{bad}`text`label,no`")
    with pytest.raises(ScriptError, match="at most 8"):
        parse("`guild_rank_any,any,no," + ",".join(f"g{n},1" for n in range(9)) + "`text`label,no`")
    parse("`guild_rank_any,any,no,aldermere,3,sunvale,3`text`label,no`")
    parse("`guild_rank_any,each,no," + ",".join(f"g{n},1" for n in range(8)) + "`text`label,no`")
