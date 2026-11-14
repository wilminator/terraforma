"""The world's bulletin: a fall and a founding post a line, the dialog tag ``news`` shows the latest, and only the latest are kept."""

import pytest
from sqlalchemy import func, select

from terraforma.models import Map
from terraforma.npcs.script import ScriptError, parse
from terraforma.realms import news, service
from terraforma.realms.models import DORMANT, NewsPost

from .test_guild_ranks import says
from .test_npc_state import a_party

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


def test_the_news_tag_is_checked_when_the_text_is_placed():
    parse("`news,3,none`Notices:`label,none`Nothing posted.")
    for bad in ("`news,0,end`", "`news,11,end`", "`news,two,end`", "`news,3`", "`news,3,nowhere`"):
        with pytest.raises(ScriptError):
            parse(bad)


async def a_town(db, hero, state="live"):
    world_id = (await db.get(Map, hero.map_id)).world_id
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    return world_id, await service.add_settlement(db, realm, hero.map_id, "fenn", "Fennick", state=state)


async def test_a_fall_and_a_founding_post_a_line_each_and_the_board_shows_the_newest_first(db):
    hero = (await a_party(db)).heroes[0]
    world_id, town = await a_town(db, hero)
    board = "`news,5,none`Posted:`end``label,none`Nothing posted."
    assert await says(db, hero, board) == "Nothing posted."
    await service.fall(db, town)
    await service.found(db, town)
    assert [each.text for each in await news.latest(db, world_id, 5)] == ["Fennick was founded again.", "Fennick has fallen."]
    assert [each.kind for each in await news.latest(db, world_id, 5)] == ["founded", "fell"]
    assert await says(db, hero, board) == "Fennick was founded again.\nFennick has fallen.\nPosted:"
    assert await says(db, hero, "`news,1,none`.`end``label,none`-") == "Fennick was founded again.\n."


async def test_a_new_village_is_founded_for_the_first_time(db):
    hero = (await a_party(db)).heroes[0]
    world_id, town = await a_town(db, hero, state=DORMANT)
    await service.found(db, town)
    assert [each.text for each in await news.latest(db, world_id, 1)] == ["Fennick was founded."]


async def test_the_bulletin_keeps_only_the_latest_posts_and_cuts_a_long_line(db):
    hero = (await a_party(db)).heroes[0]
    world_id, _town = await a_town(db, hero)
    for number in range(news.KEEP + 7):
        await news.post(db, world_id, f"line {number}")
    assert await db.scalar(select(func.count()).select_from(NewsPost)) == news.KEEP
    assert (await news.latest(db, world_id, 1))[0].text == f"line {news.KEEP + 6}"
    assert len((await news.post(db, world_id, "x" * 400)).text) == news.MAX_TEXT
