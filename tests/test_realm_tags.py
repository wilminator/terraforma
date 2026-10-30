"""The dialog tags that read and move a realm's strength: ``realm_strength`` and ``realm_nudge``."""

import copy

import pytest

from terraforma.content.schema import ContentError, check_seed
from terraforma.models import Map
from terraforma.npcs.script import ScriptError, parse
from terraforma.npcs.service import NpcError
from terraforma.realms import service as realms
from terraforma.realms.models import Realm

from .test_guild_ranks import says
from .test_npc_state import a_party
from .test_realm_supply import with_supply

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


async def a_realm(db, hero, strength=10) -> Realm:
    """A realm 'ardent' in the hero's world at $strength."""
    return await realms.create_realm(db, (await db.get(Map, hero.map_id)).world_id, "ardent", "Ardent", strength)


def test_the_realm_tags_are_checked_when_the_text_is_placed():
    parse("`realm_strength,ardent,lt,-5,weak``realm_nudge,ardent,-3``realm_nudge,ardent,12``label,weak`")
    for bad in (
        "`realm_strength,ardent,lt,5`",  # too few parameters
        "`realm_strength,ardent,below,5,end`",  # not a comparison
        "`realm_strength,ardent,lt,five,end`",  # not a number
        "`realm_strength,ardent,lt,--5,end`",  # not a number
        "`realm_strength,ardent,lt,5,nowhere`",  # label never defined
        "`realm_nudge,ardent,0`",  # moves nothing
        "`realm_nudge,ardent,+3`",  # not a whole number
        "`realm_nudge,ardent`",  # too few parameters
        "`realm_nudge,,3`",  # no realm
    ):
        with pytest.raises(ScriptError):
            parse(bad)


async def test_a_board_offers_a_rescue_quest_only_while_the_realm_is_weak(db):
    hero = (await a_party(db)).heroes[0]
    realm = await a_realm(db, hero, strength=-20)
    board = "`realm_strength,ardent,lt,0,strong`The realm needs help.`end``label,strong`All is well."
    assert await says(db, hero, board) == "The realm needs help."
    realm.strength = 0
    await db.flush()
    assert await says(db, hero, board) == "All is well.", "zero is not below zero"


async def test_a_nudge_moves_the_strength_up_or_down_and_stays_within_the_limit(db):
    hero = (await a_party(db)).heroes[0]
    realm = await a_realm(db, hero, strength=10)
    assert await says(db, hero, "`realm_nudge,ardent,5`Done.") == "Done."
    assert realm.strength == 15
    await says(db, hero, "`realm_nudge,ardent,-30`Lost.")
    assert realm.strength == -15
    await says(db, hero, "`realm_nudge,ardent,999999999`Huge.")
    assert realm.strength == realms.STRENGTH_LIMIT
    await says(db, hero, "`realm_nudge,ardent,-999999999``realm_nudge,ardent,-999999999`Huge.")
    assert realm.strength == -realms.STRENGTH_LIMIT


async def test_a_dialog_naming_a_realm_the_world_does_not_have_says_so(db):
    hero = (await a_party(db)).heroes[0]
    await a_realm(db, hero)
    for text in ("`realm_strength,nowhere,lt,5,end`", "`realm_nudge,nowhere,5`"):
        with pytest.raises(NpcError, match="there is no realm 'nowhere'"):
            await says(db, hero, text)


def test_the_seed_check_refuses_a_realm_tag_for_a_realm_the_seed_does_not_have():
    def seed(text: str, where: str):
        data = copy.deepcopy(with_supply())  # realm 'ardent'
        if where == "dialogs":
            data["dialogs"] = [{"key": "board", "text": text}]
        elif where == "npcs":
            data["npcs"] = [{"key": "clerk", "name": "Clerk", "map": "hub", "x": 1, "y": 1, "dialog": text}]
        else:
            data["maps"][0]["objects"] = [{"key": "crate", "name": "Crate", "x": 0, "y": 0, "script": text}]
        return data

    for where in ("dialogs", "npcs", "maps"):
        check_seed(seed("`realm_strength,ardent,lt,5,no``realm_nudge,ardent,-2``label,no`", where))
        for text in ("`realm_nudge,nowhere,2`", "`realm_strength,nowhere,lt,5,no``label,no`"):
            with pytest.raises(ContentError, match="names the realm 'nowhere', which realms.json doesn't have"):
                check_seed(seed(text, where))
