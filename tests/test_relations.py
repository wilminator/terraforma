"""Relationships between teams: directed, on a scale with bands, a private note, and every change the game's say."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.relations import service
from terraforma.relations.hooks import DEFAULT_BANDS, Band, Change, Ref, Relations
from terraforma.relations.models import NOTE_MAX, Relationship
from terraforma.testing import in_app_db

from .helpers import expect

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
RELATIONS = Relations()


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the first account)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def teams(db, owner, *names):
    return [await heroes.create_team(db, owner, name) for name in names]


def ref(team):
    return Ref("team", team.id)


async def count(db):
    return await db.scalar(select(func.count()).select_from(Relationship))


# --- the scale and the bands -------------------------------------------------------------------------------------

def test_the_default_bands_cover_the_whole_scale_once():
    covered = sorted(score for band in DEFAULT_BANDS for score in range(band.low, band.high + 1))
    assert covered == list(range(-100, 101))
    names = {score: RELATIONS.band(score) for score in (-100, -61, -60, -21, -20, 0, 20, 21, 60, 61, 100)}
    assert names == {-100: "enemy", -61: "enemy", -60: "wary", -21: "wary", -20: "neutral", 0: "neutral", 20: "neutral",
                     21: "friendly", 60: "friendly", 61: "close", 100: "close"}


def test_a_game_names_its_own_bands():
    class Courtly(Relations):
        bands = (Band("foe", -100, -1), Band("stranger", 0, 0), Band("kin", 1, 100))

    assert [Courtly().band(score) for score in (-5, 0, 7)] == ["foe", "stranger", "kin"]


# --- forming and changing ----------------------------------------------------------------------------------------------

@anyio
async def test_a_score_forms_the_relationship_in_one_direction_only(db):
    mike = await account(db)
    aria, bram = await teams(db, mike, "Aria", "Bram")
    row = await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), score=40))
    assert (row.subject_id, row.object_id, row.score) == (aria.id, bram.id, 40)
    assert await service.get(db, ref(bram), ref(aria)) is None, "Bram's view of Aria is its own, and has not been formed"
    await service.apply(db, RELATIONS, Change(ref(bram), ref(aria), score=-30))
    assert (await service.get(db, ref(aria), ref(bram))).score == 40 and (await service.get(db, ref(bram), ref(aria))).score == -30


@anyio
async def test_a_change_by_a_delta_and_the_scale_has_ends(db):
    mike = await account(db)
    aria, bram = await teams(db, mike, "Aria", "Bram")
    await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), delta=30))
    assert (await service.get(db, ref(aria), ref(bram))).score == 30
    await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), delta=500))
    assert (await service.get(db, ref(aria), ref(bram))).score == 100
    await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), score=-5000))
    assert (await service.get(db, ref(aria), ref(bram))).score == -100


@anyio
async def test_the_game_decides_every_change(db):
    mike = await account(db)
    aria, bram, rivals = await teams(db, mike, "Aria", "Bram", "Rivals")

    class Careful(Relations):
        async def may_form(self, session, subject, object, change):
            return await service.name_of(session, object) != "Rivals"

        async def initial(self, session, subject, object):
            return 10

        async def resolve(self, session, subject, object, current, change):
            if change.by == "player" and change.score is not None and change.score > current + 20:
                return current + 20  # trust is earned a little at a time
            return await super().resolve(session, subject, object, current, change)

    game = Careful()
    row = await service.apply(db, game, Change(ref(aria), ref(bram), score=90))
    assert row.score == 30, "it started at 10 and a player may raise it by 20"
    row = await service.apply(db, game, Change(ref(aria), ref(bram), score=90, by="game", reason="saved the day"))
    assert row.score == 90, "the game's own rules are not held to the player's limit"
    with pytest.raises(service.RelationError, match="can't form"):
        await service.apply(db, game, Change(ref(aria), ref(rivals), score=5))
    assert await service.get(db, ref(aria), ref(rivals)) is None


@anyio
async def test_the_refusals(db):
    mike = await account(db)
    (aria,) = await teams(db, mike, "Aria")
    with pytest.raises(service.RelationError, match="itself"):
        await service.apply(db, RELATIONS, Change(ref(aria), ref(aria), score=1))
    with pytest.raises(service.NotFound):
        await service.apply(db, RELATIONS, Change(ref(aria), Ref("team", 999_999), score=1))
    with pytest.raises(service.NotFound):
        await service.apply(db, RELATIONS, Change(Ref("team", 999_999), ref(aria), score=1))
    with pytest.raises(service.NotFound, match="no such alliance"):
        await service.apply(db, RELATIONS, Change(ref(aria), Ref("alliance", 1), score=1))
    (bram,) = await teams(db, mike, "Bram")
    with pytest.raises(service.RelationError, match="a score or a change"):
        await service.apply(db, RELATIONS, Change(ref(aria), ref(bram)))
    assert await count(db) == 0


# --- notes ---------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_note_is_the_subjects_own_and_forms_at_the_starting_score(db):
    mike = await account(db)
    aria, bram = await teams(db, mike, "Aria", "Bram")
    row = await service.set_note(db, RELATIONS, ref(aria), ref(bram), "  they healed us at the ford  ")
    assert (row.note, row.score) == ("they healed us at the ford", 0)
    await service.set_note(db, RELATIONS, ref(bram), ref(aria), "owes us one")
    assert (await service.get(db, ref(aria), ref(bram))).note == "they healed us at the ford"
    assert (await service.get(db, ref(bram), ref(aria))).note == "owes us one"
    await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), score=50))
    assert (await service.get(db, ref(aria), ref(bram))).note == "they healed us at the ford", "a new score keeps the note"
    with pytest.raises(service.RelationError, match="at most"):
        await service.set_note(db, RELATIONS, ref(aria), ref(bram), "x" * (NOTE_MAX + 1))


# --- reading and letting go ---------------------------------------------------------------------------------------------

@anyio
async def test_a_team_lists_only_its_own_views_by_name_with_their_bands(db):
    mike = await account(db)
    aria, cole, bram = await teams(db, mike, "Aria", "Cole", "Bram")
    await service.apply(db, RELATIONS, Change(ref(aria), ref(cole), score=-80))
    await service.apply(db, RELATIONS, Change(ref(aria), ref(bram), score=65))
    await service.set_note(db, RELATIONS, ref(aria), ref(bram), "good people")
    await service.apply(db, RELATIONS, Change(ref(cole), ref(aria), score=100))
    mine = await service.list_for(db, RELATIONS, ref(aria))
    assert [(row["name"], row["score"], row["band"], row["note"]) for row in mine] == [
        ("Bram", 65, "close", "good people"), ("Cole", -80, "enemy", ""),
    ]


@anyio
async def test_letting_go_and_a_team_leaving_the_world_take_its_relationships_with_them(db):
    mike = await account(db)
    aria, bram, cole = await teams(db, mike, "Aria", "Bram", "Cole")
    for subject, other in ((aria, bram), (bram, aria), (aria, cole), (cole, bram)):
        await service.apply(db, RELATIONS, Change(ref(subject), ref(other), score=10))
    assert await service.drop(db, ref(aria), ref(cole)) is True and await service.drop(db, ref(aria), ref(cole)) is False
    await heroes.delete_team(db, mike, bram.id)
    left = (await db.scalars(select(Relationship))).all()
    assert [(row.subject_id, row.object_id) for row in left] == [], "Bram's own and everyone's view of Bram are gone"


# --- the calls -----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path, request):
    """The game the app serves: the default relationships, or the one a test parametrizes in (``indirect``)."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    relations = getattr(request, "param", None)
    return Game(name="Test Game", seed_dir=seed_dir, **({"relations": relations} if relations else {}))


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def make_team(client, headers, name):
    return expect(client.post("/api/teams", json={"name": name}, headers=headers), 201).json()["id"]


@pytest.fixture
def pair(app_client):
    """Mike's Vanguard and Rearguard, and Zed's Rivals."""
    mike = sign_in(app_client, "Mike")
    vanguard, rearguard = make_team(app_client, mike, "Vanguard"), make_team(app_client, mike, "Rearguard")
    zed = sign_in(app_client, "Zed")
    rivals = make_team(app_client, zed, "Rivals")
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    return app_client, mike, vanguard, rearguard, rivals


def test_a_team_sets_and_reads_what_it_thinks_of_another(pair):
    client, headers, vanguard, rearguard, rivals = pair
    set_url = f"/api/teams/{vanguard}/relationships/set"
    answer = expect(client.post(set_url, json={"id": rivals, "score": -70, "note": "ambushed us"}, headers=headers), 200).json()
    assert answer == {"kind": "team", "id": rivals, "name": "Rivals", "score": -70, "band": "enemy", "note": "ambushed us"}
    expect(client.post(set_url, json={"id": rearguard, "note": "our own"}, headers=headers), 200)
    listing = expect(client.get(f"/api/teams/{vanguard}/relationships"), 200).json()
    assert [band["name"] for band in listing["bands"]] == ["enemy", "wary", "neutral", "friendly", "close"]
    assert [(row["name"], row["score"], row["note"]) for row in listing["relationships"]] == [("Rearguard", 0, "our own"), ("Rivals", -70, "ambushed us")]


def test_the_other_side_never_sees_what_a_team_thinks_of_it(pair):
    client, headers, vanguard, _rearguard, rivals = pair
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": -70, "note": "ambushed us"}, headers=headers), 200)
    zed = {"X-CSRF-Token": client.post("/api/login", json={"username": "Zed", "password": PASSWORD}).json()["csrf_token"]}
    assert expect(client.get(f"/api/teams/{rivals}/relationships"), 200).json()["relationships"] == []
    expect(client.get(f"/api/teams/{vanguard}/relationships"), 404)  # Vanguard is not Zed's team to read
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 100}, headers=zed), 404)


def test_forgetting_and_the_refusals(pair):
    client, headers, vanguard, _rearguard, rivals = pair
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 5}, headers=headers), 200)
    assert expect(client.post(f"/api/teams/{vanguard}/relationships/forget", json={"id": rivals}, headers=headers), 200).json() == {"forgotten": True}
    assert expect(client.post(f"/api/teams/{vanguard}/relationships/forget", json={"id": rivals}, headers=headers), 200).json() == {"forgotten": False}
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": vanguard, "score": 5}, headers=headers), 409)
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": 999_999, "score": 5}, headers=headers), 404)
    expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 5}), 403)  # no CSRF token


def test_the_calls_take_only_what_they_define(pair):
    client, headers, vanguard, _rearguard, rivals = pair
    url = f"/api/teams/{vanguard}/relationships/set"
    for body in (
        {"id": rivals}, {"id": rivals, "score": 101}, {"id": rivals, "score": -101}, {"id": 0, "score": 5},
        {"id": rivals, "score": 5, "kind": "guild"}, {"id": rivals, "score": 5, "why": "no"}, {"id": rivals, "note": "x" * (NOTE_MAX + 1)},
    ):
        expect(client.post(url, json=body, headers=headers), 422)


def test_setting_is_rate_limited_per_account(pair, monkeypatch):
    client, headers, vanguard, _rearguard, rivals = pair
    monkeypatch.setattr(ratelimit, "RELATION_BY_ACCOUNT", ratelimit.Limit("relation", 2, 3600))
    for _ in range(2):
        expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 5}, headers=headers), 200)
    limited = expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 5}, headers=headers), 429)
    assert "Retry-After" in limited.headers


class Slow(Relations):
    bands = (Band("foe", -100, -1), Band("kin", 0, 100))

    async def resolve(self, session, subject, object, current, change):
        return await super().resolve(session, subject, object, current, Change(change.subject, change.object, delta=min(change.score - current, 10), by=change.by))


@pytest.mark.parametrize("game", [Slow()], indirect=True)
def test_the_games_relations_name_the_bands_and_decide_the_change(pair):
    client, headers, vanguard, _rearguard, rivals = pair
    answer = expect(client.post(f"/api/teams/{vanguard}/relationships/set", json={"id": rivals, "score": 90}, headers=headers), 200).json()
    assert (answer["score"], answer["band"]) == (10, "kin"), "the game let it rise by 10 at a time"
    assert [band["name"] for band in expect(client.get(f"/api/teams/{vanguard}/relationships"), 200).json()["bands"]] == ["foe", "kin"]
