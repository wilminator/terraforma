"""Monsters an admin writes: checked like the seed's, kept when the seed loads, retired and restored, never deleted. On every database."""

import copy

import pytest
from sqlalchemy import select

from terraforma.accounts import ratelimit
from terraforma.challenge import service as challenge
from terraforma.content import models
from terraforma.content.loader import load_content
from terraforma.models import Account
from terraforma.testing import in_app_db

from .helpers import expect
from .test_content import SEED
from .test_towns import sign_in

BOSS = {"key": "ember-drake", "name": "Ember Drake", "personality": "plain", "xp_reward": 50, "gold_reward": 20,
        "stats": {"HP": 90, "Strength": 12}, "abilities": ["fire"], "items": ["potion"], "equipment": ["sword"]}


async def make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


def an_admin(client):
    """The game's content loaded, and an admin (Root) signed in; returns Root's headers (the client holds one login at a time)."""
    in_app_db(client, lambda db: load_content(db, SEED))
    root = sign_in(client, "Root")
    in_app_db(client, lambda db: make_admin(db, "Root"))
    return root


def rows(client):
    async def read(db):
        return {row.key: (row.authored, row.active) for row in (await db.scalars(select(models.Monster))).all()}

    return in_app_db(client, read)


def test_an_admin_writes_a_monster_and_it_is_marked_authored_with_its_strength(app_client):
    root = an_admin(app_client)
    made = expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 200).json()["monster"]
    assert (made["key"], made["authored"], made["active"]) == ("ember-drake", True, True)
    assert made["stats"]["HP"] == 90 and made["stats"]["Focus"] == 0, "the game's stats are all filled in"
    assert made["pxp"] > expect(app_client.get("/api/admin/monsters/slime", headers=root), 200).json()["monster"]["pxp"]
    assert rows(app_client) == {"slime": (False, True), "ember-drake": (True, True)}


def test_only_an_admin_may_look_or_write_and_a_player_is_refused_every_call(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 200)
    assert app_client.post("/api/admin/monsters", json=BOSS).status_code in (401, 403)
    mike = sign_in(app_client, "Mike")  # (now logged in as Mike)
    for call in (lambda: app_client.get("/api/admin/monsters", headers=mike), lambda: app_client.get("/api/admin/monsters/slime", headers=mike),
                 lambda: app_client.post("/api/admin/monsters", json={**BOSS, "key": "other"}, headers=mike),
                 lambda: app_client.put("/api/admin/monsters/ember-drake", json=BOSS, headers=mike),
                 lambda: app_client.post("/api/admin/monsters/ember-drake/retire", headers=mike),
                 lambda: app_client.post("/api/admin/monsters/ember-drake/restore", headers=mike)):
        expect(call(), 403)
    assert rows(app_client)["ember-drake"] == (True, True)


def test_a_monster_that_does_not_check_out_is_refused_with_what_is_wrong(app_client):
    root = an_admin(app_client)
    for bad, why in (({"personality": "ghost"}, "personality names 'ghost'"), ({"abilities": ["missing"]}, "abilities names 'missing'"),
                     ({"items": ["potion", "nope"]}, "items names 'nope'"), ({"drops": ["loot"]}, "drops names 'loot'"),
                     ({"stats": {"Luck of the draw": 5}}, "unknown stats"), ({"key": "Bad Key!"}, "key")):
        found = expect(app_client.post("/api/admin/monsters", json={**BOSS, **bad}, headers=root), 422).json()["detail"]
        assert why in found, (bad, found)
    for bad in ({"xp_reward": -1}, {"stats": {"HP": "ninety"}}, {"surprise": 1}):
        expect(app_client.post("/api/admin/monsters", json={**BOSS, **bad}, headers=root), 422)
    assert set(rows(app_client)) == {"slime"}, "nothing was written"


def test_a_key_in_use_is_a_conflict_and_a_seed_monster_cannot_be_changed_here(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json={**BOSS, "key": "slime"}, headers=root), 409)
    expect(app_client.put("/api/admin/monsters/slime", json={k: v for k, v in BOSS.items() if k != "key"}, headers=root), 409)
    expect(app_client.post("/api/admin/monsters/slime/retire", headers=root), 409)
    expect(app_client.put("/api/admin/monsters/nowhere", json={k: v for k, v in BOSS.items() if k != "key"}, headers=root), 404)
    expect(app_client.post("/api/admin/monsters/nowhere/retire", headers=root), 404)
    expect(app_client.get("/api/admin/monsters/nowhere", headers=root), 404)
    assert rows(app_client) == {"slime": (False, True)}


def test_replacing_rewrites_the_monster_whole_and_keeps_it_authored(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 200)
    body = {"name": "Cinder Drake", "personality": "plain", "stats": {"HP": 120}}
    changed = expect(app_client.put("/api/admin/monsters/ember-drake", json=body, headers=root), 200).json()["monster"]
    assert (changed["name"], changed["stats"]["HP"], changed["abilities"], changed["equipment"], changed["authored"]) == ("Cinder Drake", 120, [], [], True)
    expect(app_client.put("/api/admin/monsters/ember-drake", json={**body, "abilities": ["missing"]}, headers=root), 422)
    assert expect(app_client.get("/api/admin/monsters/ember-drake", headers=root), 200).json()["monster"]["name"] == "Cinder Drake", "a refused rewrite changes nothing"


def test_the_seed_loading_again_never_retires_an_authored_monster_but_still_retires_its_own(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 200)
    smaller = copy.deepcopy(SEED)
    smaller["monsters"] = []
    in_app_db(app_client, lambda db: load_content(db, smaller))
    assert rows(app_client) == {"slime": (False, False), "ember-drake": (True, True)}
    in_app_db(app_client, lambda db: load_content(db, SEED))
    assert rows(app_client) == {"slime": (False, True), "ember-drake": (True, True)}


def test_a_seed_row_with_an_authored_monsters_key_takes_it_over(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json={**BOSS, "key": "gremlin"}, headers=root), 200)
    taken = copy.deepcopy(SEED)
    taken["monsters"].append({"key": "gremlin", "name": "Seed Gremlin", "personality": "plain", "stats": {"HP": 5}})
    in_app_db(app_client, lambda db: load_content(db, taken))
    assert rows(app_client)["gremlin"] == (False, True)
    expect(app_client.put("/api/admin/monsters/gremlin", json={"name": "x", "personality": "plain"}, headers=root), 409)
    smaller = copy.deepcopy(SEED)
    in_app_db(app_client, lambda db: load_content(db, smaller))
    assert rows(app_client)["gremlin"] == (False, False), "the seed owns it now and retires it as it does its own"


def test_retiring_takes_a_monster_out_of_play_and_restoring_brings_it_back_without_deleting_it(app_client):
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 200)
    assert expect(app_client.post("/api/admin/monsters/ember-drake/retire", headers=root), 200).json() == {"key": "ember-drake", "active": False}
    assert rows(app_client)["ember-drake"] == (True, False)
    expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 409)  # its key stays taken
    live = expect(app_client.get("/api/admin/monsters?authored=true&active=true", headers=root), 200).json()["monsters"]
    assert live == []
    assert expect(app_client.post("/api/admin/monsters/ember-drake/restore", headers=root), 200).json()["active"] is True
    assert [row["key"] for row in expect(app_client.get("/api/admin/monsters?authored=true&active=true", headers=root), 200).json()["monsters"]] == ["ember-drake"]


def test_a_retired_ability_cannot_be_given_to_a_new_monster(app_client):
    root = an_admin(app_client)
    smaller = copy.deepcopy(SEED)
    smaller["abilities"] = [row for row in smaller["abilities"] if row["key"] != "fire"]
    smaller["monsters"] = []
    in_app_db(app_client, lambda db: load_content(db, smaller))
    found = expect(app_client.post("/api/admin/monsters", json=BOSS, headers=root), 422).json()["detail"]
    assert "abilities names 'fire'" in found


def test_the_list_pages_in_order_and_filters(app_client):
    root = an_admin(app_client)
    for number in range(3):
        expect(app_client.post("/api/admin/monsters", json={**BOSS, "key": f"drake-{number}"}, headers=root), 200)
    everything = expect(app_client.get("/api/admin/monsters", headers=root), 200).json()["monsters"]
    assert [row["key"] for row in everything] == ["slime", "drake-0", "drake-1", "drake-2"]
    page = expect(app_client.get(f"/api/admin/monsters?after={everything[1]['id']}&limit=1", headers=root), 200).json()["monsters"]
    assert [row["key"] for row in page] == ["drake-1"]
    assert [row["key"] for row in expect(app_client.get("/api/admin/monsters?authored=false", headers=root), 200).json()["monsters"]] == ["slime"]
    expect(app_client.get("/api/admin/monsters?limit=0", headers=root), 422)


def test_writing_monsters_is_rate_limited_per_account(app_client, monkeypatch):
    monkeypatch.setattr(ratelimit, "MONSTER_AUTHOR_BY_ACCOUNT", ratelimit.Limit("monster-author", 2, 3600))
    root = an_admin(app_client)
    for number in range(2):
        expect(app_client.post("/api/admin/monsters", json={**BOSS, "key": f"drake-{number}"}, headers=root), 200)
    expect(app_client.post("/api/admin/monsters", json={**BOSS, "key": "drake-9"}, headers=root), 429)
