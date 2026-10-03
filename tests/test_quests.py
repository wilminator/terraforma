"""Quest records and markers: a team's permanent record and its markers for quests in progress, on every database."""

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.heroes import service as heroes
from terraforma.models import Account
from terraforma.quests import service as quests
from terraforma.quests.models import QuestMarker, QuestRecord

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}


async def a_team(db, name="Vanguard", owner_name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    owner = await db.scalar(select(Account).where(Account.username == owner_name)) or await create_account(
        db, owner_name, PASSWORD, email=f"{owner_name.lower()}@example.com", confirmed=True)
    return owner, await heroes.create_team(db, owner, name)


async def test_a_completed_quest_is_added_to_the_teams_record_by_category_and_level(db):
    _owner, team = await a_team(db)
    assert await quests.complete(db, team.id, "hunt", 1) == 1
    assert await quests.complete(db, team.id, "hunt", 1) == 2
    assert await quests.complete(db, team.id, "hunt", 2, times=3) == 3
    assert await quests.complete(db, team.id, "fetch", 1) == 1
    assert await quests.completed(db, team.id, "hunt", 1) == 2
    assert await quests.completed(db, team.id, "hunt", 2) == 3
    assert await quests.completed(db, team.id, "hunt") == 5, "at any level"
    assert await quests.completed(db, team.id, "hunt", 9) == 0 and await quests.completed(db, team.id, "never") == 0
    assert await db.scalar(select(func.count()).select_from(QuestRecord)) == 3


async def test_records_belong_to_the_team(db):
    _owner, one = await a_team(db, "Vanguard")
    _owner, two = await a_team(db, "Rearguard")
    await quests.complete(db, one.id, "hunt", 1)
    assert await quests.completed(db, two.id, "hunt", 1) == 0


async def test_a_quest_needs_a_category_and_counts_at_least_once(db):
    _owner, team = await a_team(db)
    for bad in (("", 1, 1), ("hunt", -1, 1), ("hunt", 1, 0)):
        with pytest.raises(quests.QuestError):
            await quests.complete(db, team.id, bad[0], bad[1], bad[2])


async def test_a_marker_is_zero_until_set_and_setting_zero_clears_it(db):
    _owner, team = await a_team(db)
    assert await quests.marker(db, team.id, "rats") == 0
    await quests.set_marker(db, team.id, "rats", 2)
    await quests.set_marker(db, team.id, "rats", 3)
    assert await quests.marker(db, team.id, "rats") == 3 and await db.scalar(select(func.count()).select_from(QuestMarker)) == 1
    await quests.set_marker(db, team.id, "rats", 0)
    assert await quests.marker(db, team.id, "rats") == 0 and await db.scalar(select(func.count()).select_from(QuestMarker)) == 0
    await quests.set_marker(db, team.id, "rats", 0)  # nothing to clear: still fine
    for bad in (-1, quests.MAX_VALUE + 1):
        with pytest.raises(quests.QuestError):
            await quests.set_marker(db, team.id, "rats", bad)


@pytest.mark.parametrize("op, left, right, expected", [
    ("eq", 2, 2, True), ("eq", 2, 3, False), ("ne", 2, 3, True), ("ne", 2, 2, False),
    ("lt", 2, 3, True), ("lt", 3, 3, False), ("le", 3, 3, True), ("le", 4, 3, False),
    ("gt", 4, 3, True), ("gt", 3, 3, False), ("ge", 3, 3, True), ("ge", 2, 3, False),
])
def test_the_comparisons(op, left, right, expected):
    assert quests.compare(op, left, right) is expected


def test_an_unknown_comparison_is_refused():
    with pytest.raises(quests.QuestError, match="no comparison"):
        quests.compare("gte", 1, 1)


async def test_deleting_a_team_takes_its_record_and_markers_with_it(db):
    owner, team = await a_team(db)
    await quests.complete(db, team.id, "hunt", 1)
    await quests.set_marker(db, team.id, "rats", 1)
    await heroes.delete_team(db, owner, team.id)
    assert await db.scalar(select(func.count()).select_from(QuestRecord)) == 0 and await db.scalar(select(func.count()).select_from(QuestMarker)) == 0
