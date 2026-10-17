"""The marketplace calls over HTTP: post, search, buy, cancel, and the team's parcels."""

import pytest

from terraforma import testing
from terraforma.economy import TeamGold
from terraforma.heroes.models import Hero
from terraforma.testing import in_app_db

from .helpers import expect
from .test_trading import PASSWORD, game, sign_in, stock_over_http  # noqa: F401 (the game fixture)

pytestmark = pytest.mark.heroes

ECONOMY = TeamGold()


def login(client, username):
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def give_gold(client, hero_id, amount):
    async def work(db):
        hero = await db.get(Hero, hero_id)
        from sqlalchemy import select
        from terraforma.heroes.models import TeamMember

        team_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
        await ECONOMY.credit_team(db, team_id, amount)

    in_app_db(client, work)


@pytest.fixture
def two_teams(app_client):
    """Mike's Aria (100 gold, two potions) and Zed's Zara (200 gold); `mike()` and `zed()` sign in as them and give the CSRF headers."""
    sign_in(app_client, "Mike")
    mike = login(app_client, "Mike")
    aria = testing.make_team(app_client, mike, "Vanguard", heroes=("Aria",))["members"][0]["hero_id"]
    sign_in(app_client, "Zed")
    zed = login(app_client, "Zed")
    zara = testing.make_team(app_client, zed, "Rearguard", heroes=("Zara",))["members"][0]["hero_id"]
    give_gold(app_client, aria, 100)
    give_gold(app_client, zara, 200)
    stock_over_http(app_client, aria, "potion", 2)
    return app_client, (lambda: login(app_client, "Mike")), aria, (lambda: login(app_client, "Zed")), zara


def post_listing(client, headers, hero, **over):
    body = {"guild": "vanguard", "items": [{"position": 0, "qty": 1}], "price_gold": 40, **over}
    return client.post(f"/api/heroes/{hero}/listings", json=body, headers=headers)


def test_a_listing_is_posted_found_and_bought_then_collected(two_teams):
    client, mike, aria, zed, zara = two_teams
    listing = expect(post_listing(client, mike(), aria), 200).json()
    assert listing["goods"] == [["potion", 1]] and listing["price_gold"] == 40
    found = expect(client.get("/api/market/listings", params={"text": "potion", "max_gold": 50}), 200).json()
    assert [row["id"] for row in found] == [listing["id"]]
    assert expect(client.get("/api/market/listings", params={"guild": "elsewhere"}), 200).json() == []
    bought = expect(client.post(f"/api/heroes/{zara}/listings/{listing['id']}/buy", json={"to_guild": "vanguard"}, headers=zed()), 200).json()
    assert bought["items"] == [["potion", 1]] and bought["purpose"] == "purchase"
    assert expect(client.get("/api/market/listings"), 200).json() == []
    waiting = expect(client.get(f"/api/heroes/{zara}/parcels"), 200).json()
    assert [row["id"] for row in waiting] == [bought["id"]]
    collected = expect(client.post(f"/api/heroes/{zara}/parcels/{bought['id']}/collect", headers=zed()), 200).json()
    assert collected["state"] == "collected"
    assert expect(client.get(f"/api/heroes/{zara}/parcels"), 200).json() == []


def test_a_seller_takes_a_listing_back_and_others_cannot(two_teams):
    client, mike, aria, zed, zara = two_teams
    listing = expect(post_listing(client, mike(), aria), 200).json()
    expect(client.post(f"/api/heroes/{zara}/listings/{listing['id']}/cancel", headers=zed()), 404)
    answer = expect(client.post(f"/api/heroes/{aria}/listings/{listing['id']}/cancel", headers=mike()), 200).json()
    assert answer["state"] == "cancelled"
    expect(client.post(f"/api/heroes/{aria}/listings/{listing['id']}/cancel", headers=mike()), 409)
    expect(client.post(f"/api/heroes/{zara}/listings/{listing['id']}/buy", json={"to_guild": "vanguard"}, headers=zed()), 404)


def test_a_team_cannot_buy_its_own_listing_or_pay_what_it_lacks(two_teams):
    client, mike, aria, zed, zara = two_teams
    cheap = expect(post_listing(client, mike(), aria), 200).json()
    expect(client.post(f"/api/heroes/{aria}/listings/{cheap['id']}/buy", json={"to_guild": "vanguard"}, headers=mike()), 409)
    dear = expect(post_listing(client, mike(), aria, price_gold=5000), 409).json()  # the fee is more than Aria has
    assert "gold" in dear["detail"]


def test_only_the_owner_acts_and_only_with_the_csrf_token(two_teams):
    client, mike, aria, zed, zara = two_teams
    expect(client.post(f"/api/heroes/{aria}/listings", json={"guild": "g", "items": [{"position": 0}], "price_gold": 1}), 403)
    expect(post_listing(client, zed(), aria), 404)  # Zed does not own Aria
    expect(client.get(f"/api/heroes/{aria}/parcels"), 404)


def test_the_calls_take_only_what_they_define(two_teams):
    client, mike, aria, zed, zara = two_teams
    for body in (
        {"guild": "g", "items": [], "price_gold": 1},
        {"guild": "", "items": [{"position": 0}], "price_gold": 1},
        {"guild": "g", "items": [{"position": 0, "qty": 0}], "price_gold": 1},
        {"guild": "g", "items": [{"position": 0}], "price_gold": -1},
        {"guild": "g", "items": [{"position": 0}], "price_gold": 1, "memo": "hi"},
    ):
        expect(client.post(f"/api/heroes/{aria}/listings", json=body, headers=mike()), 422)
    expect(client.post(f"/api/heroes/{aria}/listings/1/buy", json={}, headers=mike()), 422)
    expect(client.get("/api/market/listings", params={"limit": 0}), 422)
    expect(client.get("/api/market/listings", params={"max_gold": -1}), 422)
