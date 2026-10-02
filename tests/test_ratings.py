"""Rating questions after a fight: who is asked, what the game decides, answering and dismissing, and the calls."""

import json

import pytest
from sqlalchemy import select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.fights import live, store
from terraforma.fights.build import hero_fighter, team_party
from terraforma.fights.combatant import Command
from terraforma.fights.fight import build_fight
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.models import Account
from terraforma.relations import ratings
from terraforma.relations import service as relations_service
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.models import RatingPrompt
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

from .test_fight_rules import fighter

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 40, "MP": 5, "Speed": 10, "Accuracy": 12, "Strength": 12, "Dodge": 4, "Block": 6}}]}
RULES = Rules()


# --- who did what to whom (pure) ---------------------------------------------------------------------------------------

def pvp(*debts, solo=False):
    """Party 0 has team 10 (charids 100, 101), party 1 team 20 (200) and, unless solo, a monster; debts are (on whom, by whom, ratio)."""
    mine = [fighter("A1"), fighter("A2")]
    for number, hero in enumerate(mine):
        hero.charid = 100 + number
    theirs = [fighter("B1"), fighter("Mob")]
    theirs[0].charid = 200
    fight = build_fight({0: {0: mine}, 1: {0: theirs}})
    fight.parties[0].teams, fight.parties[1].teams = {10: [100, 101]}, {20: [200]}
    for target, actor, ratio in debts:
        fight.get(target).xp_debts.append([*actor, ratio, 10])
    return fight


def test_a_team_that_was_harmed_or_helped_by_another_player_team_is_marked_for_a_question():
    fight = pvp(((0, 0, 0), (1, 0, 0), 0.4), ((1, 0, 0), (0, 0, 1), -0.2))
    assert ratings.interactions(fight) == {(10, 20): "harmed", (20, 10): "helped"}


def test_doing_both_to_a_team_is_both():
    fight = pvp(((0, 0, 0), (1, 0, 0), 0.4), ((0, 0, 1), (1, 0, 0), -0.3))
    assert ratings.interactions(fight) == {(10, 20): "both"}


def test_a_monster_a_team_mate_a_nothing_and_an_unteamed_actor_raise_no_question():
    fight = pvp(((0, 0, 0), (1, 0, 1), 0.5), ((0, 0, 0), (0, 0, 1), 0.5), ((0, 0, 0), (1, 0, 0), 0.0))
    assert ratings.interactions(fight) == {}, "a monster, someone on its own side, and a debt of nothing"
    assert ratings.interactions(pvp(((1, 0, 1), (0, 0, 0), 0.5))) == {}, "the monster is not a team that can be asked"


# --- the database ---------------------------------------------------------------------------------------------------------------

async def two_teams(db):
    if await db.scalar(select(Job.id).limit(1)) is None:
        await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    out = []
    for owner, name in ((mike, "Alpha"), (zed, "Bravo")):
        team = await service.create_team(db, owner, name)
        hero = await service.create_hero(db, owner, f"Hero {name}", "fighter")
        await service.add_to_team(db, owner, team.id, hero.id)
        out.append((owner, team, hero))
    return out


async def a_pvp_fight(db):
    (mike, alpha, aria), (zed, bravo, bram) = await two_teams(db)
    fights = {}
    for hero, team in ((aria, alpha), (bram, bravo)):
        fighters, teams = await team_party(db, team)
        fights[team.id] = (fighters, teams)
    fight = build_fight({0: {0: fights[alpha.id][0]}, 1: {0: fights[bravo.id][0]}})
    fight.parties[0].teams, fight.parties[1].teams = fights[alpha.id][1], fights[bravo.id][1]
    record = await store.create_fight(db, await ensure_start(db), fight)
    return mike, zed, alpha, bravo, aria, bram, fight, record


async def test_a_neutral_team_that_was_harmed_is_asked_once_and_an_opinionated_one_is_not(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])  # Alpha's hero owes Bravo's: Bravo harmed Alpha
    fight.get((1, 0, 0)).xp_debts.append([0, 0, 0, -0.25, 10])  # Bravo's owes Alpha's: Alpha helped Bravo
    relations = Relations()
    await relations_service.apply(db, relations, Change(Ref("team", bravo.id), Ref("team", alpha.id), score=-70))  # Bravo already dislikes Alpha
    made = await ratings.create_prompts(db, relations, record.id, fight)
    assert [(each.subject_team_id, each.object_team_id, each.interaction) for each in made] == [(alpha.id, bravo.id, "harmed")]
    assert await ratings.create_prompts(db, relations, record.id, fight) == [], "asked once per fight"


async def test_a_game_decides_whom_to_ask(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])

    class Never(Relations):
        async def ask_after_fight(self, session, subject, object, interaction, score):
            return False

    class Always(Relations):
        async def ask_after_fight(self, session, subject, object, interaction, score):
            return True

    assert await ratings.create_prompts(db, Never(), record.id, fight) == []
    await relations_service.apply(db, Always(), Change(Ref("team", alpha.id), Ref("team", bravo.id), score=-90))
    assert len(await ratings.create_prompts(db, Always(), record.id, fight)) == 1, "even one that has an opinion, when the game says so"


async def test_only_the_team_asked_sees_the_question_and_with_the_names(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])
    await ratings.create_prompts(db, Relations(), record.id, fight)
    [asked] = await ratings.pending(db, mike.id)
    assert asked["interaction"] == "harmed" and asked["fight"] == record.guid
    assert (asked["team"], asked["other"]) == ({"id": alpha.id, "name": "Alpha"}, {"id": bravo.id, "name": "Bravo"})
    assert await ratings.pending(db, zed.id) == [], "the other side is not told it is being rated"


async def test_answering_rates_the_team_through_the_games_rule_and_closes_the_question(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])
    [prompt] = await ratings.create_prompts(db, Relations(), record.id, fight)

    class Cautious(Relations):
        async def resolve(self, session, subject, object, current, change):
            return min(await super().resolve(session, subject, object, current, change), 20)

    view = await relations_service.view(db, Cautious(), await ratings.answer(db, Cautious(), mike.id, prompt.id, 90))
    assert view["score"] == 20, "the game's rule decides what the answer comes to"
    assert prompt.state == "answered" and await ratings.pending(db, mike.id) == []
    with pytest.raises(relations_service.RelationError, match="dealt with"):
        await ratings.answer(db, Relations(), mike.id, prompt.id, 10)


async def test_nobody_else_can_answer_or_dismiss_and_dismissing_changes_nothing(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])
    [prompt] = await ratings.create_prompts(db, Relations(), record.id, fight)
    with pytest.raises(relations_service.NotFound):
        await ratings.answer(db, Relations(), zed.id, prompt.id, 50)
    with pytest.raises(relations_service.NotFound):
        await ratings.dismiss(db, zed.id, prompt.id)
    with pytest.raises(relations_service.NotFound):
        await ratings.dismiss(db, mike.id, 999)
    await ratings.dismiss(db, mike.id, prompt.id)
    assert prompt.state == "dismissed" and await relations_service.get(db, Ref("team", alpha.id), Ref("team", bravo.id)) is None


async def test_a_team_that_goes_takes_its_questions_with_it(db):
    mike, zed, alpha, bravo, _a, _b, fight, record = await a_pvp_fight(db)
    fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])
    await ratings.create_prompts(db, Relations(), record.id, fight)
    await service.delete_team(db, zed, bravo.id)
    assert await db.scalar(select(RatingPrompt.id)) is None


async def test_a_player_fight_played_to_the_end_asks_the_teams_that_hurt_each_other(db):
    mike, zed, alpha, bravo, aria, bram, _fight, record = await a_pvp_fight(db)
    for _ in range(60):
        if record.finished:
            break
        await live.submit_command(db, record, mike.id, (0, 0, 0), Command.ATTACK_LEFT, 0, (1, 0, 0), RULES)
        await live.submit_command(db, record, zed.id, (1, 0, 0), Command.ATTACK_LEFT, 0, (0, 0, 0), RULES)
        await live.resolve_round(db, record, RULES, None, Relations())
    assert record.finished
    asked = {(each.subject_team_id, each.object_team_id) for each in (await db.scalars(select(RatingPrompt))).all()}
    assert (alpha.id, bravo.id) in asked or (bravo.id, alpha.id) in asked, "at least the team that was hit is asked"
    assert {(each["team"]["id"], each["other"]["id"]) for each in await ratings.pending(db, mike.id)} <= {(alpha.id, bravo.id)}


# --- the calls ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    return Game(name="Test", seed_dir=seed_dir)


@pytest.fixture
def client(app_client):
    async def setup(db):
        mike, zed, alpha, bravo, aria, bram, fight, record = await a_pvp_fight(db)
        fight.get((0, 0, 0)).xp_debts.append([1, 0, 0, 0.5, 10])
        [prompt] = await ratings.create_prompts(db, Relations(), record.id, fight)
        return prompt.id, bravo.id

    app_client.prompt_id, app_client.other_team = in_app_db(app_client, setup)
    return app_client


def log_in(client, username="Mike"):
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def test_the_questions_are_listed_for_the_team_that_was_asked(client):
    assert client.get("/api/ratings").status_code == 401
    log_in(client)
    shown = client.get("/api/ratings").json()
    assert [band["name"] for band in shown["bands"]][2] == "neutral"
    [asked] = shown["questions"]
    assert asked["id"] == client.prompt_id and asked["other"]["name"] == "Bravo" and asked["interaction"] == "harmed"
    log_in(client, "Zed")
    assert client.get("/api/ratings").json()["questions"] == []


def test_a_rating_is_answered_once_with_a_login_the_token_and_a_score_in_range(client):
    url = f"/api/ratings/{client.prompt_id}/answer"
    assert client.post(url, json={"score": 50}).status_code == 401
    headers = log_in(client)
    assert client.post(url, json={"score": 50}).status_code == 403, "no CSRF token"
    for bad in ({"score": 101}, {"score": -101}, {"score": "9"}, {}, {"score": 5, "extra": 1}):
        assert client.post(url, json=bad, headers=headers).status_code == 422
    done = client.post(url, json={"score": 55}, headers=headers)
    assert done.status_code == 200 and done.json()["score"] == 55 and done.json()["band"] == "friendly"
    assert client.post(url, json={"score": 10}, headers=headers).status_code == 409
    assert client.get("/api/ratings").json()["questions"] == []


def test_a_question_can_be_dismissed_and_is_not_anyone_elses_to_answer(client):
    headers = log_in(client, "Zed")
    assert client.post(f"/api/ratings/{client.prompt_id}/answer", json={"score": 10}, headers=headers).status_code == 404
    assert client.post(f"/api/ratings/{client.prompt_id}/dismiss", headers=headers).status_code == 404
    headers = log_in(client)
    assert client.post(f"/api/ratings/{client.prompt_id}/dismiss", headers=headers).json() == {"dismissed": True}
    assert client.post(f"/api/ratings/{client.prompt_id}/dismiss", headers=headers).status_code == 409
    assert client.post("/api/ratings/999/dismiss", headers=headers).status_code == 404
