"""Add-ons (force disuse): a component an admin has made ``disused``, the scene its heroes go through when their player signs in, what
is set aside and what comes back, and the fights a hero can't join until it is settled."""

import dataclasses

import pytest
from sqlalchemy import select

from terraforma.content.models import Ability, Item, Job
from terraforma.disuse import gate
from terraforma.disuse.hooks import Disuse, Lost
from terraforma.disuse.models import DisuseTalk, SetAside
from terraforma.example import GAME as EXAMPLE
from terraforma.fights import live
from terraforma.fights.rules import Rules
from terraforma.heroes import inventory
from terraforma.heroes import service as heroes_service
from terraforma.heroes.models import Hero, HeroAbility, Team
from terraforma.npcs.script import ScriptError, Who, advance, parse
from terraforma.npcs.state import DialogState
from terraforma.testing import in_app_db

from .helpers import expect
from .test_addon_components import an_admin, commit, components, pack
from .test_towns import sign_in

pytestmark = [pytest.mark.anyio, pytest.mark.platform]

#: The pack's mage class, its fire ability and one of its tonics retired; the rest of the pack stays on (the drops and the slime need
#: the tonic and the fire, so they are off: a component that is on may not need one that is not).
RETIRED = {"states": {"pack": {"jobs": {"pack_mage": "disused"}, "abilities": {"pack_fire": "disused"},
                               "items": {"pack_tonic": "disused", "pack_elixir": "on", "pack_plain": "on"}}}}


@pytest.fixture
def addons():
    return ["pack"]


@pytest.fixture
def game(tmp_path):
    return dataclasses.replace(EXAMPLE, addons=(pack(tmp_path),))


def everything(client):
    section = client.app.state.state_sections["components"]
    return section.switch(section.default(), "pack", True)


async def a_hero(db, hero_id):
    return await db.get(Hero, hero_id)


def merlin(client):
    """An admin (who plays too) with Merlin, a pack mage who knows fire and carries three tonics and an elixir, in a game with the whole pack on."""
    root = an_admin(client)
    commit(client, root, everything(client))
    expect(client.post("/api/teams", json={"name": "Mages", "heroes": [{"name": "Merlin", "job": "pack_mage"}]}, headers=root), 201)
    hero_id = expect(client.get("/api/heroes", headers=root), 200).json()[0]["id"]

    async def gear(db):
        hero = await a_hero(db, hero_id)
        hero.level, hero.xp = 4, 321
        await inventory.add_item(db, hero, "pack_tonic", 3)
        await inventory.add_item(db, hero, "pack_elixir", 1)

    in_app_db(client, gear)
    return root, hero_id


def url(hero_id, *more):
    return "/".join([f"/api/heroes/{hero_id}/disuse", *more]).rstrip("/")


def walk(client, root, hero_id, pick="Fighter"):
    """Starts the hero's scene and goes through it: Next at every pause, and the choice named $pick when asked a question. The scene's
    last frame, with every event it showed (``events``) and every prompt (``asked``) put together."""
    frame = expect(client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    events, asked = list(frame["events"]), []
    while not frame["ended"]:
        prompt = frame["prompt"]
        asked.append(prompt)
        body = {} if prompt["type"] == "ack" else {"choice": [option["text"] for option in prompt["options"]].index(pick)}
        frame = expect(client.post(url(hero_id, "next"), json=body, headers=root), 200).json()
        events += frame["events"]
    return {**frame, "events": events, "asked": asked}


def said(events):
    return "".join(event["text"] for event in events if event["type"] == "text")


async def what_hero_has(db, hero_id):
    hero = await a_hero(db, hero_id)
    job = await db.get(Job, hero.job_id)
    abilities = {ability.key for ability in await inventory.known_abilities(db, hero)}
    items = {item.key: stack.qty for stack, item in await inventory.stacks(db, hero)}
    kept = {(row.kind, row.key): row.qty for row in await db.scalars(select(SetAside).where(SetAside.hero_id == hero_id))}
    return {"job": job.key, "abilities": abilities, "items": items, "kept": kept, "level": hero.level, "xp": hero.xp}


async def fight_for(db, hero_id):
    """Tries to start a fight for the hero's team: the reason it was refused, or None."""
    hero = await a_hero(db, hero_id)
    from terraforma.heroes.models import TeamMember

    team = await db.get(Team, await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id)))
    try:
        await live.start_team_fight(db, team, ["slime"], Rules())
    except live.Refused as error:
        return str(error)
    return None


# --- the state ---------------------------------------------------------------------------------------------------------------

def test_disused_is_a_state_a_bulk_switch_can_set_and_a_row_that_needs_it_may_not_be_on(tmp_path):
    section, _ = components(tmp_path)
    assert section.check(RETIRED, {}) == []
    assert section.switch(section.default(), "pack", "disused", "items", "tonics") == {"states": {"pack": {"items": {"pack_tonic": "disused"}}}}
    both = section.switch(section.switch(section.default(), "pack", True, "items"), "pack", "disused", "items", "tonics")
    assert both["states"]["pack"]["items"] == {"pack_tonic": "disused", "pack_elixir": "on", "pack_plain": "on"}
    assert section.switch(both, "pack", False, "items", "tonics")["states"]["pack"]["items"] == {"pack_elixir": "on", "pack_plain": "on"}
    assert section.check(section.switch(section.switch(section.default(), "pack", True), "pack", "disused", "abilities"), {}) == [
        "pack: jobs 'pack_mage' is on, but it needs abilities 'pack_fire', which is not on",
        "pack: monsters 'pack_slime' is on, but it needs abilities 'pack_fire', which is not on"]
    with pytest.raises(ValueError, match="switched on"):
        section.switch(section.default(), "pack", "gone")


def test_a_commit_marks_the_rows_disused_and_switching_back_clears_it(app_client):
    root = an_admin(app_client)

    async def flags(db):
        return {row.key: (row.active, row.disused) for row in [*await db.scalars(select(Job).where(Job.key == "pack_mage")),
                                                              *await db.scalars(select(Item).where(Item.key.like("pack_%")))]}

    commit(app_client, root, RETIRED)
    assert in_app_db(app_client, flags) == {"pack_mage": (False, True), "pack_tonic": (False, True), "pack_elixir": (True, False), "pack_plain": (True, False)}
    commit(app_client, root, everything(app_client))
    assert not any(disused for _active, disused in in_app_db(app_client, flags).values())
    commit(app_client, root, app_client.app.state.state_sections["components"].default())
    assert in_app_db(app_client, flags) == {key: (False, False) for key in ("pack_mage", "pack_tonic", "pack_elixir", "pack_plain")}


# --- what a hero has to stop using -----------------------------------------------------------------------------------------

def test_nothing_happens_to_a_hero_until_its_player_asks(app_client):
    root, hero_id = merlin(app_client)
    before = in_app_db(app_client, lambda db: what_hero_has(db, hero_id))
    assert before["job"] == "pack_mage" and before["abilities"] == {"pack_fire"} and before["items"] == {"pack_tonic": 3, "pack_elixir": 1}
    commit(app_client, root, RETIRED)
    assert in_app_db(app_client, lambda db: what_hero_has(db, hero_id)) == before, "(the commit does not touch heroes: a player who never returns costs nothing)"
    seen = expect(app_client.get(url(hero_id), headers=root), 200).json()
    assert seen == {"pending": True, "talking": False, "prompt": None, "restorable": [],
                    "lost": {"job": {"key": "pack_mage", "name": "Pack mage"}, "abilities": [{"key": "pack_fire", "name": "Fire"}],
                             "items": [{"key": "pack_tonic", "name": "Pack tonic", "qty": 3}]}}
    assert [(hero["name"], hero["job"]) for hero in expect(app_client.get("/api/heroes", headers=root), 200).json()] == [("Merlin", "pack_mage")]


def test_a_hero_with_nothing_disused_has_nothing_to_settle(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/teams", json={"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]}, headers=root), 201)
    hero_id = expect(app_client.get("/api/heroes", headers=root), 200).json()[0]["id"]
    commit(app_client, root, everything(app_client))
    assert expect(app_client.get(url(hero_id), headers=root), 200).json()["pending"] is False
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert (frame["ended"], frame["pending"], frame["events"]) == (True, False, [])


# --- the scene and the fights --------------------------------------------------------------------------------------------------

def test_the_default_scene_is_a_black_screen_that_says_what_is_lost_and_asks_for_a_new_class(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    reason = in_app_db(app_client, lambda db: fight_for(db, hero_id))
    assert reason == "Merlin has to settle what the game has retired before they fight (their player has to sign in)"

    scene = walk(app_client, root, hero_id)
    assert scene["events"][0] == {"type": "scene", "name": "black"}
    words = said(scene["events"])
    assert "asleep" in words and "sorry" in words
    assert "Fire and Pack tonic (x3)" in words and "put away, safe" in words, words
    assert "Your class, Pack mage, is gone" in words and "choose a new class" in words
    choice = next(prompt for prompt in scene["asked"] if prompt["type"] == "choice")
    assert [option["text"] for option in choice["options"]] == ["Fighter", "Scout", "Mage", "Cleric"], "(the active jobs: not the retired one)"
    assert scene["pending"] is False and scene["ended"] is True

    now = in_app_db(app_client, lambda db: what_hero_has(db, hero_id))
    assert now["job"] == "fighter" and (now["level"], now["xp"]) == (4, 321), "(the new class costs nothing)"
    assert now["abilities"] == {"cleave", "power_strike"} and now["items"] == {"pack_elixir": 1}, "(fire is put away; the fighter learns what its class grants at level 4)"
    assert now["kept"] == {("job", "pack_mage"): 1, ("ability", "pack_fire"): 1, ("item", "pack_tonic"): 3}, "(nothing is deleted)"
    assert in_app_db(app_client, lambda db: fight_for(db, hero_id)) is None, "(settled: the fight starts)"
    assert expect(app_client.get(url(hero_id), headers=root), 200).json()["pending"] is False


def test_a_class_has_to_be_picked_and_a_scene_that_ends_without_one_leaves_the_hero_blocked(app_client, tmp_path):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)

    class NoClassesOpen(Disuse):
        async def jobs(self, session, hero, lost):
            return []

    app_client.app.state.game = dataclasses.replace(app_client.app.state.game, disuse=NoClassesOpen())
    scene = walk(app_client, root, hero_id)
    assert "no other is open to you yet" in said(scene["events"]) and scene["ended"] is True
    assert scene["pending"] is True, "(the job is still disused)"
    now = in_app_db(app_client, lambda db: what_hero_has(db, hero_id))
    assert now["job"] == "pack_mage" and now["abilities"] == set(), "(what could be put away was; the class stays until a new one is chosen)"
    assert in_app_db(app_client, lambda db: fight_for(db, hero_id)) is not None
    assert expect(app_client.get(url(hero_id), headers=root), 200).json()["pending"] is True


def test_the_scene_resumes_where_the_player_left_it_and_may_be_started_again(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert frame["prompt"]["type"] == "ack" and frame["ended"] is False
    seen = expect(app_client.get(url(hero_id), headers=root), 200).json()
    assert seen["talking"] is True and seen["prompt"] == {"type": "ack", "accepts": {"choice": [], "next": True, "cancel": False}}
    assert in_app_db(app_client, lambda db: db.scalar(select(DisuseTalk.id).where(DisuseTalk.hero_id == hero_id))) is not None
    again = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert again["events"] == frame["events"], "(it starts over)"
    expect(app_client.post(url(hero_id, "next"), json={"choice": 0}, headers=root), 409)  # (an ack takes Next, not a choice)
    assert walk(app_client, root, hero_id)["ended"] is True
    assert in_app_db(app_client, lambda db: db.scalar(select(DisuseTalk.id).where(DisuseTalk.hero_id == hero_id))) is None
    expect(app_client.post(url(hero_id, "next"), json={}, headers=root), 409)  # (no scene to go on in)


def test_only_a_heros_own_player_can_read_or_run_its_scene(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    zed = sign_in(app_client, "Zed")
    expect(app_client.get(url(hero_id), headers=zed), 404)
    expect(app_client.post(url(hero_id, "start"), json={}, headers=zed), 404)
    expect(app_client.post(url(hero_id, "next"), json={}, headers=zed), 404)
    expect(app_client.post(url(hero_id, "start"), json={}), 403)  # (no CSRF token)


def test_a_hero_in_a_fight_waits_for_its_scene(app_client):
    root, hero_id = merlin(app_client)
    assert in_app_db(app_client, lambda db: fight_for(db, hero_id)) is None  # (a fight is on, and the hero in it)
    commit(app_client, root, RETIRED)
    assert "is in a fight" in expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 409).json()["detail"]


def test_a_party_waits_for_a_hero_whose_player_has_not_come_back(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)

    async def both(db):
        return await gate.blocked(db, [hero_id]), await gate.blocked(db, []), await gate.blocked(db, [hero_id + 1000])

    assert in_app_db(app_client, both) == ("Merlin has to settle what the game has retired before they fight (their player has to sign in)", None, None)


# --- what comes back -----------------------------------------------------------------------------------------------------------

def test_turning_the_component_back_on_gives_the_hero_back_what_was_set_aside(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    walk(app_client, root, hero_id)
    commit(app_client, root, everything(app_client))
    seen = expect(app_client.get(url(hero_id), headers=root), 200).json()
    assert seen["pending"] is False
    assert seen["restorable"] == [{"kind": "ability", "name": "Fire", "qty": 1}, {"kind": "item", "name": "Pack tonic", "qty": 3}]
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert frame["ended"] is True and frame["restored"] == {"abilities": ["Fire"], "items": [{"name": "Pack tonic", "qty": 3}]}
    now = in_app_db(app_client, lambda db: what_hero_has(db, hero_id))
    assert now["abilities"] == {"pack_fire", "cleave", "power_strike"} and now["items"] == {"pack_tonic": 3, "pack_elixir": 1}
    assert now["job"] == "fighter" and now["kept"] == {("job", "pack_mage"): 1}, "(the hero keeps the class it chose)"
    assert expect(app_client.get(url(hero_id), headers=root), 200).json()["restorable"] == []


def test_what_is_kept_for_a_hero_whose_pack_is_full_waits_for_room(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    walk(app_client, root, hero_id)  # (the elixir is all that is left in the pack)
    commit(app_client, root, everything(app_client))

    async def fill(db):
        hero = await a_hero(db, hero_id)
        for _ in range(inventory.MAX_ITEMS - 1):
            assert await inventory.add_item(db, hero, "short_sword", 1) == 0

    async def make_room(db):
        await inventory.remove_item(db, await a_hero(db, hero_id), 1, 1)

    in_app_db(app_client, fill)
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert frame["restored"] == {"abilities": ["Fire"], "items": []}, "(the ability came back; the tonics wait)"
    assert in_app_db(app_client, lambda db: what_hero_has(db, hero_id))["kept"][("item", "pack_tonic")] == 3
    in_app_db(app_client, make_room)
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert frame["restored"] == {"abilities": [], "items": [{"name": "Pack tonic", "qty": 3}]}
    assert ("item", "pack_tonic") not in in_app_db(app_client, lambda db: what_hero_has(db, hero_id))["kept"]


def test_a_disused_ability_or_item_that_is_only_off_again_comes_back_too(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    walk(app_client, root, hero_id)
    commit(app_client, root, app_client.app.state.state_sections["components"].default())  # (off: no new use, not disused)
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert frame["restored"]["abilities"] == ["Fire"], "(what a hero already had keeps working when it is only off)"


def test_deleting_a_hero_deletes_what_was_kept_for_it_and_its_scene(app_client):
    root, hero_id = merlin(app_client)
    commit(app_client, root, RETIRED)
    expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200)
    hero_id_kept = hero_id

    async def count(db):
        return [len((await db.scalars(select(table.id).where(table.hero_id == hero_id_kept))).all()) for table in (DisuseTalk, SetAside)]

    assert in_app_db(app_client, count) == [1, 0]
    walk(app_client, root, hero_id)
    assert in_app_db(app_client, count) == [0, 3]
    expect(app_client.post(f"/api/teams/{expect(app_client.get('/api/teams', headers=root), 200).json()[0]['id']}/delete", json={}, headers=root), 200)
    assert in_app_db(app_client, count) == [0, 0]


# --- a game's own scene ------------------------------------------------------------------------------------------------------

class DreamWorld(Disuse):
    """A game that stages its own scene: it names the scene, tells a hero that lost a job from one that did not, and asks nothing."""

    async def scene(self, session, hero, lost):
        return ("`scene,dream`The gods speak.\n`lost,job,keeps_class`Your class is gone.\n`set_job,mage,end`"
                "`label,keeps_class`You keep your class.\n`lost,item,end`Your things are in the temple.")


def test_a_game_writes_its_scene_with_its_own_staging_and_the_lost_and_set_job_tags(app_client):
    root, hero_id = merlin(app_client)
    app_client.app.state.game = dataclasses.replace(app_client.app.state.game, disuse=DreamWorld())
    commit(app_client, root, {"states": {"pack": {"items": {"pack_tonic": "disused", "pack_elixir": "on", "pack_plain": "on"}}}})
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert said(frame["events"]) == "The gods speak.\nYou keep your class.\nYour things are in the temple."
    assert frame["events"][0] == {"type": "scene", "name": "dream"} and frame["ended"] is True and frame["pending"] is False
    now = in_app_db(app_client, lambda db: what_hero_has(db, hero_id))
    assert now["job"] == "pack_mage" and now["items"] == {"pack_elixir": 1} and now["kept"] == {("item", "pack_tonic"): 3}


def test_a_scene_that_is_not_valid_dialog_is_refused_not_run(app_client):
    root, hero_id = merlin(app_client)

    class Broken(Disuse):
        async def scene(self, session, hero, lost):
            return "`nothing_like_a_tag`"

    app_client.app.state.game = dataclasses.replace(app_client.app.state.game, disuse=Broken())
    commit(app_client, root, RETIRED)
    assert "the game's scene is not valid dialog" in expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 409).json()["detail"]


def test_set_job_refuses_a_job_that_is_not_offered_and_the_scene_goes_on_at_its_label(app_client):
    root, hero_id = merlin(app_client)

    class Pushy(Disuse):
        async def scene(self, session, hero, lost):
            return "`set_job,pack_mage,again`Done.`end``label,again`That class is gone.\n`set_job,fighter,end`You are a fighter."

    app_client.app.state.game = dataclasses.replace(app_client.app.state.game, disuse=Pushy())
    commit(app_client, root, RETIRED)
    frame = expect(app_client.post(url(hero_id, "start"), json={}, headers=root), 200).json()
    assert said(frame["events"]) == "That class is gone.\nYou are a fighter." and frame["pending"] is False
    assert in_app_db(app_client, lambda db: what_hero_has(db, hero_id))["job"] == "fighter"


# --- the dialog language ------------------------------------------------------------------------------------------------------

def test_the_scene_set_job_and_lost_tags_are_checked_when_the_text_is(tmp_path):
    parse("`scene,black``set_job,fighter,end``lost,job,end``lost,ability,end``lost,item,end`")
    for text, why in (("`scene`", "takes 1 parameter"), ("`scene,`", "needs a name"), ("`set_job,fighter`", "takes 2 parameters"),
                      ("`set_job,,end`", "needs a job"), ("`set_job,fighter,nowhere`", "no label 'nowhere'"), ("`lost,hat,end`", "what is lost is one of job, ability, item"),
                      ("`lost,job`", "takes 2 parameters")):
        with pytest.raises(ScriptError, match=why):
            parse(text)


async def test_a_scene_tag_stages_a_scene_and_the_state_tags_work_only_in_a_scene():
    async def tag(command, parts):
        return None

    result = await advance(parse("`scene,dream`Hi"), 0, None, None, Who(), tag)
    assert result["events"] == [{"type": "scene", "name": "dream"}, {"type": "text", "text": "Hi"}]
    for command, parts in (("set_job", ["fighter", "end"]), ("lost", ["job", "end"])):
        with pytest.raises(ScriptError, match="only works in a force-disuse dialog"):
            await DialogState(None, None)(command, parts)


def test_lost_says_what_is_gone_and_names_come_out_safe_to_put_in_dialog():
    assert Lost().empty and not Lost(job=("a", "A")).empty
    assert Lost(job=("a", "A"), abilities=(("f", "Fire"),), items=(("t", "Tonic", 2),)).view() == {
        "job": {"key": "a", "name": "A"}, "abilities": [{"key": "f", "name": "Fire"}], "items": [{"key": "t", "name": "Tonic", "qty": 2}]}
