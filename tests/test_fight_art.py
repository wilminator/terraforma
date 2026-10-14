"""The fight's pictures: a personality's ``fight_art`` reaches the fight view for the monsters that have the personality and for the heroes
whose job has the personality's key, and a fighter without any is left plain. On every database."""

import copy

import pytest

from terraforma.content.loader import load_content
from terraforma.fights import live
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.heroes.models import Team

from .test_fight_store import SEED
from .test_live_fights import a_team

pytestmark = pytest.mark.anyio

RULES = Rules()
SHEET = {"sheet": "crystal", "frame": 1, "animations": "crystal"}
STORED = {**SHEET, "colors": None}  # (what the content keeps: every field of the reference)


def drawn(plain=None, fighter=None):
    """The fight store's seed (a job ``fighter``, a monster ``ogre`` whose personality is ``plain``) with $plain as the art of the
    personality ``plain`` and, if $fighter is given, a personality ``fighter`` (the job's key) with that as its art."""
    seed = copy.deepcopy(SEED)
    seed["personalities"][0]["fight_art"] = plain
    if fighter is not None:
        seed["personalities"].append({"key": "fighter", "name": "Fighter", "fight_art": fighter})
    return seed


async def started(db, seed):
    """A team fight against an ogre after the content is loaded again from $seed."""
    _hero, mike, team = await a_team(db)
    mike_id, team_id = mike.id, team.id
    await load_content(db, seed)
    team = await db.get(Team, team_id)
    return mike_id, await live.start_team_fight(db, team, ["ogre"], RULES)


async def test_a_monster_is_drawn_by_its_personality_and_a_hero_by_the_personality_of_its_jobs_key(db):
    mike_id, record = await started(db, drawn(plain=SHEET, fighter="aria.png"))

    aria, ogre = (await live.view(db, record, RULES, mike_id))["fighters"]
    assert aria["art"] == "aria.png" and ogre["art"] == STORED

    stranger = await live.view(db, record, RULES)
    assert [each["art"] for each in stranger["fighters"]] == ["aria.png", STORED]


async def test_a_fighter_with_no_art_has_no_art_key(db):
    mike_id, record = await started(db, drawn(plain="ogre.png"))  # (no personality has the job's key: the hero stays a card)
    record_id = record.id
    aria, ogre = (await live.view(db, record, RULES, mike_id))["fighters"]
    assert "art" not in aria and ogre["art"] == "ogre.png"

    await load_content(db, drawn(fighter=SHEET))  # (the monsters' personality lost its art, the hero's job got a personality)
    record = await db.get(FightRecord, record_id)
    aria, ogre = (await live.view(db, record, RULES, mike_id))["fighters"]
    assert aria["art"] == STORED and "art" not in ogre
