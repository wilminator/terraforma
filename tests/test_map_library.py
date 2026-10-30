"""Maps kept for reuse: a generated map is saved under the game's key, and the next place that wants one like it is made from the copy."""

import pytest
from sqlalchemy import func, select

from terraforma.maps import library
from terraforma.maps.models import MapObject
from terraforma.models import Map
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


async def a_crypt(db, name="crypt-1"):
    """A generated 2 by 2 map with a chest and an edge event."""
    hub = await ensure_start(db)
    game_map = Map(world_id=hub.world_id, name=name, title="Crypt", width=2, height=2, safe_steps=4, wrap_x=True,
                   tileset=[{"name": "stone"}], tiles=[[0, 0], [0, 0]], zones=[{"name": "dark"}], zone_tiles=[[0, 0], [0, 0]])
    db.add(game_map)
    await db.flush()
    db.add(MapObject(map_id=game_map.id, key="chest", name="Chest", kind="chest", action="open", dialog="say,Empty", x=1, y=1))
    db.add(MapObject(map_id=game_map.id, key="edge-north", name="north", kind="edge", action="walk", dialog="say,Out", x=0, y=0, edge="north"))
    await db.flush()
    return game_map


async def test_a_saved_map_makes_copies_of_its_content_and_objects(db):
    original = await a_crypt(db)
    saved = await library.save(db, original, "crypt-small")
    copy = await library.instantiate(db, saved, "crypt-2", title="Crypt II")
    assert (copy.id != original.id, copy.name, copy.title) == (True, "crypt-2", "Crypt II")
    assert (copy.width, copy.height, copy.wrap_x, copy.safe_steps, copy.tiles, copy.zone_tiles) == (2, 2, True, 4, [[0, 0], [0, 0]], [[0, 0], [0, 0]])
    rows = (await db.scalars(select(MapObject).where(MapObject.map_id == copy.id).order_by(MapObject.key))).all()
    assert [(each.key, each.kind, each.x, each.y, each.edge, each.dialog) for each in rows] == [
        ("chest", "chest", 1, 1, None, "say,Empty"), ("edge-north", "edge", 0, 0, "north", "say,Out")]
    assert saved.uses == 1


async def test_changing_a_copy_never_changes_the_saved_map_or_another_copy(db):
    saved = await library.save(db, await a_crypt(db), "crypt-small")
    first = await library.instantiate(db, saved, "crypt-2")
    second = await library.instantiate(db, saved, "crypt-3")
    (await db.scalar(select(MapObject).where(MapObject.map_id == first.id, MapObject.key == "chest"))).dialog = "say,Taken"
    first.title = "Changed"
    await db.flush()
    assert (await db.scalar(select(MapObject.dialog).where(MapObject.map_id == second.id, MapObject.key == "chest"))) == "say,Empty"
    assert [each["dialog"] for each in saved.objects if each["key"] == "chest"] == ["say,Empty"]
    assert second.title == "Crypt"


async def test_a_key_is_saved_once_unless_it_is_replaced(db):
    first = await a_crypt(db)
    await library.save(db, first, "crypt-small")
    with pytest.raises(library.LibraryError, match="already a saved map"):
        await library.save(db, first, "crypt-small")
    first.title = "Newer"
    assert (await library.save(db, first, "crypt-small", replace=True)).title == "Newer"
    assert [each.key for each in await library.listing(db, first.world_id)] == ["crypt-small"]


async def test_a_copy_needs_a_free_valid_name(db):
    original = await a_crypt(db)
    saved = await library.save(db, original, "crypt-small")
    with pytest.raises(library.LibraryError, match="already a map"):
        await library.instantiate(db, saved, original.name)
    with pytest.raises(library.LibraryError):
        await library.instantiate(db, saved, "Not A Slug")
    with pytest.raises(library.LibraryError):
        await library.save(db, original, "")


async def test_reuse_or_make_generates_once_and_copies_after_when_review_is_off(db):
    hub = await ensure_start(db)
    calls = []

    def maker(name):
        async def make():
            calls.append(name)
            return await a_crypt(db, name)
        return make

    first = await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-1", maker("crypt-1"), review=False)
    second = await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-2", maker("crypt-2"), review=False)
    third = await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-3", maker("crypt-3"), review=False)
    assert calls == ["crypt-1"], "generated once; the rest are copies"
    assert [first.name, second.name, third.name] == ["crypt-1", "crypt-2", "crypt-3"]
    assert (await library.find(db, hub.world_id, "crypt-small")).uses == 2
    assert await db.scalar(select(func.count()).select_from(MapObject).where(MapObject.map_id == third.id)) == 2


async def test_a_generated_map_waits_for_an_admin_before_it_is_copied(db):
    hub = await ensure_start(db)
    calls = []

    def maker(name):
        async def make():
            calls.append(name)
            return await a_crypt(db, name)
        return make

    await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-1", maker("crypt-1"))
    saved = await library.find(db, hub.world_id, "crypt-small")
    assert saved.status == library.PROPOSED
    await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-2", maker("crypt-2"))
    assert calls == ["crypt-1", "crypt-2"], "still generated while it waits, and the proposed copy is not replaced"
    assert saved.uses == 0 and (saved.objects, saved.title) == (saved.objects, "Crypt")
    await library.review(db, saved.id, True)
    third = await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-3", maker("crypt-3"))
    assert calls == ["crypt-1", "crypt-2"] and third.name == "crypt-3" and saved.uses == 1, "approved: copied"


async def test_a_rejected_map_is_never_copied_and_a_decision_is_made_once(db):
    hub = await ensure_start(db)
    saved = await library.save(db, await a_crypt(db), "crypt-small")
    assert (await library.review(db, saved.id, False)).status == library.REJECTED
    with pytest.raises(library.LibraryError, match="already rejected"):
        await library.review(db, saved.id, True)
    with pytest.raises(library.LibraryError, match="no such saved map"):
        await library.review(db, 999, True)
    calls = []

    async def make():
        calls.append(1)
        return await a_crypt(db, "crypt-9")

    await library.reuse_or_make(db, hub.world_id, "crypt-small", "crypt-9", make)
    assert calls == [1] and saved.uses == 0


async def test_replacing_a_saved_map_needs_a_new_review(db):
    original = await a_crypt(db)
    saved = await library.save(db, original, "crypt-small", review=False)
    assert saved.status == library.APPROVED
    again = await library.save(db, original, "crypt-small", replace=True)
    assert again.status == library.PROPOSED


async def test_forgetting_a_saved_map_leaves_the_maps_made_from_it(db):
    saved = await library.save(db, await a_crypt(db), "crypt-small")
    copy = await library.instantiate(db, saved, "crypt-2")
    assert await library.forget(db, saved.world_id, "crypt-small") is True
    assert await library.forget(db, saved.world_id, "crypt-small") is False
    assert await db.get(Map, copy.id) is not None


def test_an_admin_looks_over_the_saved_maps_and_decides(app_client):
    from .test_manual_quests import an_admin
    from .test_towns import sign_in

    assert app_client.get("/api/admin/saved-maps").status_code == 401
    sign_in(app_client, "Zed")
    assert app_client.get("/api/admin/saved-maps").status_code == 403, "not an admin"
    root = an_admin(app_client)

    async def make(db):
        first = await library.save(db, await a_crypt(db), "crypt-small")
        second = await library.save(db, await a_crypt(db, "crypt-2"), "crypt-big")
        return first.id, second.id

    small, big = in_app_db(app_client, make)
    from .helpers import expect

    listed = expect(app_client.get("/api/admin/saved-maps"), 200).json()["saved_maps"]
    assert [(each["key"], each["status"], each["objects"], each["uses"]) for each in listed] == [("crypt-big", "proposed", 2, 0), ("crypt-small", "proposed", 2, 0)]
    assert app_client.post(f"/api/admin/saved-maps/{small}/approve").status_code == 403, "no token"
    assert expect(app_client.post(f"/api/admin/saved-maps/{small}/approve", headers=root), 200).json()["saved_map"]["status"] == "approved"
    expect(app_client.post(f"/api/admin/saved-maps/{small}/reject", headers=root), 422)
    assert expect(app_client.post(f"/api/admin/saved-maps/{big}/reject", headers=root), 200).json()["saved_map"]["status"] == "rejected"
    expect(app_client.post(f"/api/admin/saved-maps/{big}/forget", headers=root), 200)
    expect(app_client.post(f"/api/admin/saved-maps/{big}/forget", headers=root), 404)
    expect(app_client.post("/api/admin/saved-maps/999/approve", headers=root), 404)
    assert [each["key"] for each in expect(app_client.get("/api/admin/saved-maps"), 200).json()["saved_maps"]] == ["crypt-small"]
