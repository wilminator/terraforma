"""The Saved maps section of the admin page in a real browser: maps a game kept for reuse, approved, turned down or dropped (the calls behind it are
tests/test_map_library.py)."""

from playwright.sync_api import expect

from terraforma.maps import library
from terraforma.models import Map
from terraforma.world.start import ensure_start

from .test_admin import admin_page, tab


def keep(live_server, *keys):
    """Saved maps in the database, waiting for review: one for each key."""

    async def work(db):
        hub = await ensure_start(db)
        for number, key in enumerate(keys):
            game_map = Map(world_id=hub.world_id, name=f"crypt-{number}", title="Crypt", width=3, height=2, tileset=[{"name": "stone"}], tiles=[[0] * 3] * 2,
                           zones=[{"name": "dark"}], zone_tiles=[[0] * 3] * 2)
            db.add(game_map)
            await db.flush()
            await library.save(db, game_map, key)

    live_server.in_db(work)


def open_section(page):
    tab(page, "Saved maps")
    expect(page.locator("#admin-saved-maps")).to_be_visible()


def test_with_nothing_kept_the_section_says_so(page, live_server):
    admin_page(page, live_server)
    open_section(page)
    expect(page.locator("#saved-maps-none")).to_have_text("No saved maps.")


def test_a_saved_map_is_approved(page, live_server):
    keep(live_server, "crypt-small")
    admin_page(page, live_server)
    open_section(page)
    row = page.locator("[data-key='crypt-small']")
    expect(row).to_contain_text("Waiting")
    expect(row).to_contain_text("Crypt, 3 by 2, 0 objects, copied 0 times")
    page.get_by_role("button", name="Approve crypt-small").click()
    expect(page.locator("#saved-maps-note")).to_have_text("crypt-small will be copied.")
    expect(row).to_have_attribute("data-status", "approved")
    expect(page.get_by_role("button", name="Approve crypt-small")).to_have_count(0)


def test_a_saved_map_is_turned_down_and_another_dropped(page, live_server):
    keep(live_server, "crypt-small", "crypt-big")
    admin_page(page, live_server)
    open_section(page)
    page.get_by_role("button", name="Turn down crypt-small").click()
    expect(page.locator("#saved-maps-note")).to_have_text("crypt-small was turned down.")
    expect(page.locator("[data-key='crypt-small']")).to_have_attribute("data-status", "rejected")
    page.get_by_role("button", name="Drop crypt-big").click()
    expect(page.locator("#saved-maps-note")).to_have_text("crypt-big was dropped.")
    expect(page.locator("#saved-maps-list [data-saved-map]")).to_have_count(1)
