"""The contract calls over HTTP: offer, accept, decline, withdraw and the team's view."""

import pytest

from terraforma import testing
from terraforma.economy import TeamGold
from terraforma.heroes.models import TeamMember

from terraforma.testing import in_app_db

from .helpers import expect
from .test_marketplace_routes import give_gold, login
from .test_trading import game, sign_in  # noqa: F401 (the game fixture)

pytestmark = pytest.mark.heroes

ECONOMY = TeamGold()


@pytest.fixture
def two_teams(app_client):
    """Mike's Aria and Zed's Zara, a thousand gold each; `mike()` and `zed()` sign in as them and give the CSRF headers. Also their team ids."""
    sign_in(app_client, "Mike")
    mike = lambda: login(app_client, "Mike")  # noqa: E731
    aria_team = testing.make_team(app_client, mike(), "Vanguard", heroes=("Aria",))
    sign_in(app_client, "Zed")
    zed = lambda: login(app_client, "Zed")  # noqa: E731
    zara_team = testing.make_team(app_client, zed(), "Rearguard", heroes=("Zara",))
    aria, zara = aria_team["members"][0]["hero_id"], zara_team["members"][0]["hero_id"]
    give_gold(app_client, aria, 1000)
    give_gold(app_client, zara, 1000)
    return app_client, mike, aria, aria_team["id"], zed, zara, zara_team["id"]


def offer(client, headers, hero, to_team, **over):
    body = {
        "guild": "vanguard", "to_team_id": to_team, "my_terms": [{"kind": "truce", "params": {}}],
        "their_terms": [{"kind": "truce", "params": {}}], "my_escrow": 100, "their_escrow": 100, "duration": 3600, **over,
    }
    return client.post(f"/api/heroes/{hero}/contracts", json=body, headers=headers)


def test_an_offer_is_accepted_and_both_sides_see_it(two_teams):
    client, mike, aria, _a_team, zed, zara, z_team = two_teams
    made = expect(offer(client, mike(), aria, z_team), 200).json()
    assert made["state"] == "offered" and made["a"]["escrow"] == 100 and made["b"]["team"] == "Rearguard"
    assert [(t["side"], t["kind"]) for t in made["terms"]] == [("a", "truce"), ("b", "truce")]
    assert expect(client.get(f"/api/heroes/{aria}/contracts/{made['id']}"), 200).json()["id"] == made["id"]  # (still signed in as Mike)
    accepted = expect(client.post(f"/api/heroes/{zara}/contracts/{made['id']}/accept", headers=zed()), 200).json()
    assert accepted["state"] == "active" and accepted["ends_at"] == accepted["starts_at"] + 3600
    listed = expect(client.get(f"/api/heroes/{zara}/contracts"), 200).json()
    assert [row["id"] for row in listed] == [made["id"]]


def test_the_asked_side_declines_and_the_offering_side_withdraws(two_teams):
    client, mike, aria, _a_team, zed, zara, z_team = two_teams
    first = expect(offer(client, mike(), aria, z_team), 200).json()
    declined = expect(client.post(f"/api/heroes/{zara}/contracts/{first['id']}/decline", headers=zed()), 200).json()
    assert declined["state"] == "declined"
    second = expect(offer(client, mike(), aria, z_team), 200).json()
    expect(client.post(f"/api/heroes/{zara}/contracts/{second['id']}/withdraw", headers=zed()), 404)  # not Zed's offer
    withdrawn = expect(client.post(f"/api/heroes/{aria}/contracts/{second['id']}/withdraw", headers=mike()), 200).json()
    assert withdrawn["state"] == "withdrawn"
    expect(client.post(f"/api/heroes/{zara}/contracts/{second['id']}/accept", headers=zed()), 404)


def test_refusals_are_conflicts_and_strangers_cannot_look(two_teams):
    client, mike, aria, a_team, zed, zara, z_team = two_teams
    assert "itself" in expect(offer(client, mike(), aria, a_team), 409).json()["detail"]
    assert "escrow" in expect(offer(client, mike(), aria, z_team, my_escrow=0, their_escrow=0, my_terms=[{"kind": "truce", "params": {"value": 500}}]), 409).json()["detail"]
    expect(offer(client, mike(), aria, z_team, my_terms=[{"kind": "nonsense", "params": {}}]), 409)
    made = expect(offer(client, mike(), aria, z_team), 200).json()
    sign_in(client, "Quin")
    quin = testing.make_team(client, login(client, "Quin"), "Third", heroes=("Quill",))["members"][0]["hero_id"]
    expect(client.get(f"/api/heroes/{quin}/contracts/{made['id']}"), 404)
    assert expect(client.get(f"/api/heroes/{quin}/contracts"), 200).json() == []


def test_only_the_owner_acts_and_only_with_the_csrf_token(two_teams):
    client, mike, aria, _a_team, zed, zara, z_team = two_teams
    body = {"guild": "g", "to_team_id": z_team, "duration": 60, "my_terms": [{"kind": "truce"}]}
    expect(client.post(f"/api/heroes/{aria}/contracts", json=body), 403)
    expect(offer(client, zed(), aria, z_team), 404)  # Zed does not own Aria


def test_the_calls_take_only_what_they_define(two_teams):
    client, mike, aria, _a_team, _zed, _zara, z_team = two_teams
    headers = mike()
    for over in (
        {"duration": 0}, {"to_team_id": 0}, {"guild": ""}, {"my_escrow": -1}, {"memo": "hi"},
        {"my_terms": [{"kind": "", "params": {}}]}, {"my_terms": [{"kind": "truce", "extra": 1}]}, {"my_terms": [{"kind": "truce", "params": []}]},
    ):
        expect(offer(client, headers, aria, z_team, **over), 422)


def test_a_team_sees_and_takes_an_open_contract_and_the_second_is_too_late(two_teams):
    client, mike, aria, _a_team, zed, zara, _z_team = two_teams
    from terraforma.contracts import service
    from terraforma.contracts.hooks import Contracts

    class Work(Contracts):
        def kinds(self):
            return {"slay"}

        def open_kinds(self):
            return {"slay"}

    async def post(db):
        return (await service.post_open(db, Work(), "vanguard", [("slay", {})], 50, 200, 3600)).id

    contract_id = in_app_db(client, post)
    mike()
    open_ones = expect(client.get(f"/api/heroes/{aria}/open-contracts", params={"guild": "vanguard"}), 200).json()
    assert [(row["id"], row["open"], row["reward"]) for row in open_ones] == [(contract_id, True, 200)]
    assert expect(client.get(f"/api/heroes/{aria}/open-contracts", params={"guild": "elsewhere"}), 200).json() == []
    accepted = expect(client.post(f"/api/heroes/{zara}/contracts/{contract_id}/accept", headers=zed()), 200).json()
    assert accepted["state"] == "active" and accepted["b"]["team"] == "Rearguard" and accepted["a"]["team"] is None
    expect(client.post(f"/api/heroes/{aria}/contracts/{contract_id}/accept", headers=mike()), 404)  # too late
    assert expect(client.get(f"/api/heroes/{aria}/open-contracts"), 200).json() == []


def test_an_offer_can_carry_a_payment_that_both_sides_see(two_teams):
    client, mike, aria, _a_team, _zed, _zara, z_team = two_teams
    made = expect(offer(client, mike(), aria, z_team, payment=50), 200).json()
    assert made["payment"] == 50
    assert expect(client.get(f"/api/heroes/{aria}/contracts/{made['id']}"), 200).json()["payment"] == 50
    expect(offer(client, mike(), aria, z_team, payment=-1), 422)


def test_entering_an_instance_is_refused_for_a_contract_that_hires_no_one_to_enter(two_teams):
    client, mike, aria, _a_team, zed, zara, z_team = two_teams
    made = expect(offer(client, mike(), aria, z_team), 200).json()
    expect(client.post(f"/api/heroes/{zara}/contracts/{made['id']}/accept", headers=zed()), 200)
    assert "no instance" in expect(client.post(f"/api/heroes/{zara}/contracts/{made['id']}/enter-instance", headers=zed()), 409).json()["detail"]
    expect(client.post(f"/api/heroes/{zara}/contracts/99999/enter-instance", headers=zed()), 404)
