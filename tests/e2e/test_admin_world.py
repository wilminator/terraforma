"""The World section of the admin page in a real browser: a realm's strength is moved, a settlement falls and is founded again, and the sources of a
supply are cut (the calls behind it are tests/test_realms.py and tests/test_realm_supply.py)."""

from playwright.sync_api import expect

from terraforma.models import Map
from terraforma.realms import service
from terraforma.realms.models import Material, SettlementSupply
from terraforma.world.start import ensure_start

from .test_admin import admin_page, tab


def make_world(live_server):
    """The realm Ardent (strength 10) with a live town, Ardent City, keeping 20 of 50 iron."""

    async def work(db):
        hub = await ensure_start(db)
        game_map = Map(world_id=hub.world_id, name="north", title="North")
        db.add(game_map)
        await db.flush()
        realm = await service.create_realm(db, hub.world_id, "ardent", "Ardent", strength=10)
        town = await service.add_settlement(db, realm, game_map.id, "capital", "Ardent City", tier=2)
        iron = Material(key="iron", name="Iron")
        db.add(iron)
        await db.flush()
        db.add(SettlementSupply(settlement_id=town.id, material_id=iron.id, level=20, capacity=50, rate=0, drain=0))

    live_server.in_db(work)


def open_section(page):
    tab(page, "World")
    expect(page.locator("#admin-world")).to_be_visible()


def test_with_no_realms_the_section_says_so(page, live_server):
    admin_page(page, live_server)
    open_section(page)
    expect(page.locator("#world-none")).to_have_text("The world has no realms yet.")


def test_a_realms_strength_is_moved(page, live_server):
    make_world(live_server)
    admin_page(page, live_server)
    open_section(page)
    expect(page.locator("[data-realm='ardent']")).to_contain_text("Strength 10")
    page.get_by_label("Strength of Ardent", exact=True).fill("42")
    page.get_by_role("button", name="Set the strength of Ardent").click()
    expect(page.locator("#world-note")).to_have_text("Ardent now has strength 42.")
    expect(page.locator("[data-realm='ardent']")).to_contain_text("Strength 42")


def test_a_settlement_falls_and_is_founded_again(page, live_server):
    make_world(live_server)
    admin_page(page, live_server)
    open_section(page)
    town = page.locator("[data-settlement='capital']")
    expect(town).to_contain_text("Ardent, tier 2")
    expect(town).to_have_attribute("data-state", "live")
    page.get_by_role("button", name="Let Ardent City fall").click()
    expect(page.locator("#world-note")).to_have_text("Ardent City fell.")
    expect(town).to_have_attribute("data-state", "ruins")
    page.get_by_role("button", name="Found Ardent City").click()
    expect(page.locator("#world-note")).to_have_text("Ardent City is founded.")
    expect(town).to_have_attribute("data-state", "live")


def test_the_sources_of_a_supply_are_cut_and_put_back(page, live_server):
    make_world(live_server)
    admin_page(page, live_server)
    open_section(page)
    page.get_by_role("button", name="Show the supplies of Ardent City").click()
    supply = page.locator("[data-material='iron']")
    expect(supply).to_contain_text("Iron: 20 of 50")
    page.get_by_label("Cut of Iron in Ardent City", exact=True).fill("75")
    page.get_by_role("button", name="Set the cut of Iron in Ardent City").click()
    expect(page.locator("#world-note")).to_have_text("Iron in Ardent City is cut by 75%.")
    expect(supply).to_contain_text("cut 75%")
    page.get_by_label("Cut of Iron in Ardent City", exact=True).fill("0")
    page.get_by_role("button", name="Set the cut of Iron in Ardent City").click()
    expect(supply).to_contain_text("cut 0%")
