"""Planned and live state: the draft, its check, commit and revert, the frozen flag, and the admin's calls. On every database."""

import pytest
from sqlalchemy import func, select

from terraforma.challenge import service as challenge
from terraforma.game import Game
from terraforma.gamestate import document as documents
from terraforma.gamestate import service
from terraforma.gamestate.document import Section, hash_of, sections_by_name
from terraforma.gamestate.models import LiveState, PlannedState, Snapshot
from terraforma.models import Account
from terraforma.testing import in_app_db

from .helpers import expect
from .test_towns import sign_in

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


class Rates(Section):
    """A section with a default and a check of its own."""

    name = "rates"

    def default(self):
        return {"xp": 1.0}

    def check(self, value, document):
        return [] if isinstance(value.get("xp"), int | float) and value["xp"] >= 0 else ["xp must be a number, 0 or more"]


class Bonus(Section):
    """A section that depends on another one (the whole document is passed for that)."""

    name = "bonus"

    def check(self, value, document):
        return ["bonus needs rates.xp above 1"] if value.get("on") and document["sections"]["rates"]["xp"] <= 1 else []


SECTIONS = sections_by_name([Rates(), Bonus()])


# --- the document ------------------------------------------------------------------------------------------------------

def test_equal_documents_have_equal_hashes_whatever_the_key_order():
    first = {"format": 1, "sections": {"rates": {"xp": 2, "gold": 3}, "bonus": {}}}
    second = {"sections": {"bonus": {}, "rates": {"gold": 3, "xp": 2}}, "format": 1}
    assert hash_of(first) == hash_of(second)
    assert hash_of(first) != hash_of({"format": 1, "sections": {"rates": {"xp": 2, "gold": 4}, "bonus": {}}})
    assert len(hash_of(first)) == 64


def test_the_document_refuses_what_json_cannot_carry_exactly():
    with pytest.raises(ValueError):
        documents.text_of({"x": float("nan")})
    with pytest.raises(ValueError):
        documents.text_of({"x": {1, 2}})


def test_sections_need_distinct_names():
    with pytest.raises(ValueError):
        sections_by_name([Rates(), Rates()])
    with pytest.raises(ValueError):
        sections_by_name([Section()])


def test_the_check_names_every_kind_of_problem():
    good = documents.new_document(SECTIONS)
    assert documents.problems_in(good, SECTIONS) == []
    for bad, why in (
        ([], "exactly 'format' and 'sections'"),
        ({**good, "extra": 1}, "exactly 'format' and 'sections'"),
        ({**good, "format": 2}, "format is 2"),
        ({**good, "sections": []}, "'sections' must be an object"),
        ({**good, "sections": {**good["sections"], "ghost": {}}}, "no section called 'ghost'"),
        ({**good, "sections": {"rates": {"xp": 1.0}}}, "the section is missing"),
        ({**good, "sections": {**good["sections"], "rates": 5}}, "a section must be an object"),
        ({**good, "sections": {**good["sections"], "rates": {"xp": -1}}}, "xp must be a number"),
        ({**good, "sections": {**good["sections"], "bonus": {"on": True}}}, "bonus needs rates.xp above 1"),
        ({**good, "sections": {**good["sections"], "rates": {"xp": float("inf")}}}, "not plain JSON"),
    ):
        assert any(why in problem.message for problem in documents.problems_in(bad, SECTIONS)), (bad, why)


def test_a_document_over_the_size_limit_is_a_problem(monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 50)
    assert any("bytes" in problem.message for problem in documents.problems_in(documents.new_document(SECTIONS), SECTIONS))


# --- the first state ---------------------------------------------------------------------------------------------------

async def test_the_first_look_makes_the_empty_state_live_with_a_matching_draft(db):
    current = await service.state(db, SECTIONS)
    assert current.live.document == documents.new_document(SECTIONS) == current.planned.document
    assert (current.live.hash, current.live.committed_by, current.planned.revision, current.planned.frozen) == (hash_of(current.live.document), None, 0, False)
    assert not current.differs
    assert current.live.hash == current.planned_hash


async def test_looking_again_changes_nothing(db):
    first = await service.state(db, SECTIONS)
    again = await service.state(db, SECTIONS)
    assert again.live.id == first.live.id
    assert await db.scalar(select(func.count()).select_from(Snapshot)) == 1
    assert await db.scalar(select(func.count()).select_from(LiveState)) == 1
    assert await db.scalar(select(func.count()).select_from(PlannedState)) == 1


# --- editing the draft -------------------------------------------------------------------------------------------------

async def test_an_edit_changes_the_draft_and_never_the_live_state(db):
    before = await service.state(db, SECTIONS)
    after = await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    assert after.planned.document["sections"]["rates"] == {"xp": 2}
    assert (after.planned.revision, after.differs) == (1, True)
    assert after.live.id == before.live.id and after.live.document == before.live.document
    assert (await service.live_snapshot(db, SECTIONS)).document["sections"]["rates"] == {"xp": 1.0}


async def test_an_edit_to_an_unknown_section_is_refused(db):
    with pytest.raises(service.NoSuchSection):
        await service.edit_section(db, SECTIONS, "ghost", {}, None)


async def test_an_edit_made_since_the_admin_read_the_draft_is_refused_not_lost(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    with pytest.raises(service.Conflict):
        await service.edit_section(db, SECTIONS, "rates", {"xp": 3}, None, revision=0)
    assert (await service.state(db, SECTIONS)).planned.document["sections"]["rates"] == {"xp": 2}
    await service.edit_section(db, SECTIONS, "rates", {"xp": 3}, None, revision=1)


async def test_an_edit_that_json_cannot_hold_or_that_is_too_large_is_refused(db, monkeypatch):
    with pytest.raises(service.StateError):
        await service.edit_section(db, SECTIONS, "rates", {"xp": float("nan")}, None)
    monkeypatch.setattr(documents, "MAX_BYTES", 80)
    with pytest.raises(service.StateError):
        await service.edit_section(db, SECTIONS, "rates", {"note": "x" * 200}, None)
    assert (await service.state(db, SECTIONS)).planned.revision == 0


async def test_a_draft_may_hold_a_mistake_until_it_is_checked(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": -5}, None)
    problems = await service.check(db, SECTIONS)
    assert [(problem.section, problem.message) for problem in problems] == [("rates", "xp must be a number, 0 or more")]


async def test_floats_come_back_exactly_so_the_hash_holds(db):
    value = {"xp": 0.1 + 0.2, "tiny": 1e-9}
    await service.edit_section(db, SECTIONS, "rates", value, None)
    db.expire_all()
    current = await service.state(db, SECTIONS)
    assert current.planned.document["sections"]["rates"] == value
    assert current.planned_hash == hash_of({"format": 1, "sections": {"rates": value, "bonus": {}}})


# --- commit and revert -------------------------------------------------------------------------------------------------

async def test_a_clean_draft_commits_into_a_new_live_snapshot_with_who_and_why(db):
    account = (await challenge_account(db))
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, account)
    planned = await service.state(db, SECTIONS)
    done = await service.commit(db, SECTIONS, account, planned.planned_hash, "double xp")
    assert done.live.document["sections"]["rates"] == {"xp": 2}
    assert (done.live.committed_by, done.live.note, done.live.hash) == (account, "double xp", planned.planned_hash)
    assert done.live.id != planned.live.id and not done.differs
    assert [snapshot.note for snapshot in await service.history(db)] == ["double xp", "first state"]
    assert [snapshot.id for snapshot in await service.history(db, after=done.live.id)] == [planned.live.id]
    assert len(await service.history(db, limit=1)) == 1


async def test_a_draft_with_problems_is_not_committed_and_nothing_changes(db):
    await service.edit_section(db, SECTIONS, "bonus", {"on": True}, None)
    planned = await service.state(db, SECTIONS)
    with pytest.raises(service.Refused) as refused:
        await service.commit(db, SECTIONS, None, planned.planned_hash)
    assert [problem.section for problem in refused.value.problems] == ["bonus"]
    assert (await service.state(db, SECTIONS)).live.id == planned.live.id
    assert await db.scalar(select(func.count()).select_from(Snapshot)) == 1


async def test_a_commit_of_a_draft_the_admin_did_not_see_is_refused(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    seen = (await service.state(db, SECTIONS)).planned_hash
    await service.edit_section(db, SECTIONS, "rates", {"xp": 9}, None)
    with pytest.raises(service.Conflict):
        await service.commit(db, SECTIONS, None, seen)
    assert (await service.state(db, SECTIONS)).live.document["sections"]["rates"] == {"xp": 1.0}


async def test_there_is_nothing_to_commit_when_the_draft_equals_live(db):
    current = await service.state(db, SECTIONS)
    with pytest.raises(service.Refused):
        await service.commit(db, SECTIONS, None, current.planned_hash)
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    await service.edit_section(db, SECTIONS, "rates", {"xp": 1.0}, None)  # (back to what is live)
    with pytest.raises(service.Refused):
        await service.commit(db, SECTIONS, None, current.planned_hash)


async def test_a_state_committed_again_has_the_same_hash_as_before(db):
    first = (await service.state(db, SECTIONS)).live.hash
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    two = await service.commit(db, SECTIONS, None, (await service.state(db, SECTIONS)).planned_hash)
    await service.edit_section(db, SECTIONS, "rates", {"xp": 1.0}, None)
    back = await service.commit(db, SECTIONS, None, (await service.state(db, SECTIONS)).planned_hash)
    assert back.live.hash == first != two.live.hash
    assert back.live.id != two.live.id


async def test_a_revert_drops_the_edits_and_leaves_live_alone(db):
    await service.edit_section(db, SECTIONS, "rates", {"xp": 2}, None)
    reverted = await service.revert(db, SECTIONS, None)
    assert reverted.planned.document == reverted.live.document and not reverted.differs
    assert reverted.planned.revision == 2, "a revert is an edit too, so a stale reader is refused"


# --- the frozen flag ---------------------------------------------------------------------------------------------------

def imported(xp):
    return {"format": 1, "sections": {"rates": {"xp": xp}, "bonus": {}}}


async def test_an_import_freezes_the_draft_and_every_other_change_unfreezes_it(db):
    frozen = await service.freeze(db, SECTIONS, imported(5), None)
    assert (frozen.planned.frozen, frozen.planned.document, frozen.differs) == (True, imported(5), True)
    assert (await service.edit_section(db, SECTIONS, "rates", {"xp": 6}, None)).planned.frozen is False
    assert (await service.freeze(db, SECTIONS, imported(5), None)).planned.frozen is True
    assert (await service.revert(db, SECTIONS, None)).planned.frozen is False
    done = await service.freeze(db, SECTIONS, imported(5), None)
    assert (await service.commit(db, SECTIONS, None, done.planned_hash)).planned.frozen is False


async def test_a_document_that_does_not_check_out_cannot_be_frozen(db):
    with pytest.raises(service.Refused):
        await service.freeze(db, SECTIONS, {"format": 1, "sections": {"rates": {"xp": -1}, "bonus": {}}}, None)
    assert (await service.state(db, SECTIONS)).planned.frozen is False


async def challenge_account(db):
    """An account to commit as (committed_by is a real account)."""
    from terraforma.accounts.service import create_account

    from .test_towns import PASSWORD

    account = await create_account(db, "Root", PASSWORD, email="root@example.com", confirmed=True)
    return account.id


# --- the admin's calls -------------------------------------------------------------------------------------------------

@pytest.fixture
def game():
    return Game(name="Test Game", state_sections=(Rates(), Bonus()))


async def make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


def an_admin(client):
    root = sign_in(client, "Root")
    in_app_db(client, lambda db: make_admin(db, "Root"))
    return root


def test_the_first_look_by_an_admin_makes_the_live_state(app_client):
    root = an_admin(app_client)
    seen = expect(app_client.get("/api/admin/state", headers=root), 200).json()
    assert (seen["live"]["note"], seen["planned"]["revision"], seen["planned"]["differs"], seen["planned"]["frozen"]) == ("first state", 0, False, False)
    live = expect(app_client.get("/api/admin/state/live", headers=root), 200).json()
    assert live["document"] == {"format": 1, "sections": {"bonus": {}, "rates": {"xp": 1.0}}} and live["snapshot"]["hash"] == seen["live"]["hash"]


def test_an_admin_edits_checks_and_commits_and_the_live_state_changes_only_then(app_client):
    root = an_admin(app_client)
    edited = expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 3}}, headers=root), 200).json()
    assert (edited["planned"]["revision"], edited["planned"]["differs"]) == (1, True)
    assert expect(app_client.get("/api/admin/state/live", headers=root), 200).json()["document"]["sections"]["rates"] == {"xp": 1.0}
    assert expect(app_client.get("/api/admin/state/planned", headers=root), 200).json()["document"]["sections"]["rates"] == {"xp": 3}
    checked = expect(app_client.post("/api/admin/state/check", headers=root), 200).json()
    assert (checked["ok"], checked["problems"], checked["hash"]) == (True, [], edited["planned"]["hash"])
    done = expect(app_client.post("/api/admin/state/commit", json={"hash": checked["hash"], "note": "triple xp"}, headers=root), 200).json()
    assert (done["live"]["hash"], done["live"]["note"], done["planned"]["differs"]) == (checked["hash"], "triple xp", False)
    assert expect(app_client.get("/api/admin/state/live", headers=root), 200).json()["document"]["sections"]["rates"] == {"xp": 3}
    history = expect(app_client.get("/api/admin/state/history", headers=root), 200).json()["snapshots"]
    assert [entry["note"] for entry in history] == ["triple xp", "first state"]
    assert history[0]["committed_by"] is not None
    assert [entry["note"] for entry in expect(app_client.get(f"/api/admin/state/history?after={history[0]['id']}&limit=5", headers=root), 200).json()["snapshots"]] == ["first state"]


def test_a_commit_with_problems_answers_with_each_problem_and_commits_nothing(app_client):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/bonus", json={"value": {"on": True}}, headers=root), 200)
    checked = expect(app_client.post("/api/admin/state/check", headers=root), 200).json()
    assert checked["ok"] is False and checked["problems"] == [{"section": "bonus", "message": "bonus needs rates.xp above 1"}]
    refused = expect(app_client.post("/api/admin/state/commit", json={"hash": checked["hash"]}, headers=root), 422).json()["detail"]
    assert refused["problems"] == checked["problems"] and "nothing was committed" in refused["message"]
    assert len(expect(app_client.get("/api/admin/state/history", headers=root), 200).json()["snapshots"]) == 1


def test_stale_reads_conflict_and_unknown_sections_are_not_found(app_client):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 3}}, headers=root), 200)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 4}, "revision": 0}, headers=root), 409)
    expect(app_client.post("/api/admin/state/commit", json={"hash": "0" * 64}, headers=root), 409)
    expect(app_client.put("/api/admin/state/planned/sections/ghost", json={"value": {}}, headers=root), 404)
    expect(app_client.post("/api/admin/state/commit", json={"hash": "0" * 64}, headers=root), 409)


def test_a_revert_brings_the_draft_back_to_live(app_client):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {"xp": 3}}, headers=root), 200)
    reverted = expect(app_client.post("/api/admin/state/revert", headers=root), 200).json()
    assert reverted["planned"]["differs"] is False
    assert expect(app_client.get("/api/admin/state/planned", headers=root), 200).json()["document"]["sections"]["rates"] == {"xp": 1.0}


def test_only_an_admin_with_the_csrf_token_may_look_or_change_anything(app_client):
    root = an_admin(app_client)
    reads = (lambda h: app_client.get("/api/admin/state", headers=h), lambda h: app_client.get("/api/admin/state/live", headers=h),
             lambda h: app_client.get("/api/admin/state/planned", headers=h), lambda h: app_client.get("/api/admin/state/history", headers=h))
    changes = (lambda h: app_client.post("/api/admin/state/check", headers=h), lambda h: app_client.post("/api/admin/state/revert", headers=h),
               lambda h: app_client.post("/api/admin/state/commit", json={"hash": "0" * 64}, headers=h),
               lambda h: app_client.put("/api/admin/state/planned/sections/rates", json={"value": {}}, headers=h))
    for call in changes:
        assert call({}).status_code in (401, 403), "a change without the CSRF token is refused, even for the admin"
    for call in reads:
        expect(call(root), 200)
    mike = sign_in(app_client, "Mike")  # (a player now: the client holds one login at a time)
    for call in reads + changes:
        expect(call(mike), 403)
    app_client.cookies.clear()
    for call in reads + changes:
        assert call({}).status_code in (401, 403)


def test_the_calls_take_only_what_they_name(app_client):
    root = an_admin(app_client)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {}, "surprise": 1}, headers=root), 422)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": [1]}, headers=root), 422)
    expect(app_client.put("/api/admin/state/planned/sections/rates", json={"value": {}, "revision": -1}, headers=root), 422)
    expect(app_client.post("/api/admin/state/commit", json={"hash": "short"}, headers=root), 422)
    expect(app_client.post("/api/admin/state/commit", json={"hash": "0" * 64, "note": "x" * 201}, headers=root), 422)
    expect(app_client.get("/api/admin/state/history?limit=0", headers=root), 422)


def test_a_game_with_no_sections_still_has_a_live_state():
    from terraforma.gamestate.document import new_document

    assert new_document({}) == {"format": 1, "sections": {}}
