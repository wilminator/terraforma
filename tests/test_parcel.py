"""Collecting the store's parcel: items the store granted go to a hero's side through the game's storage, and what does not fit waits."""

import pytest
from sqlalchemy import select

from terraforma import testing
from terraforma.models import Account
from terraforma.fights.rules import Rules
from terraforma.heroes.models import Hero
from terraforma.storage.hooks import AccountStorage, NoStorage, TeamStorage
from terraforma.storegrant import service as grants
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import look, sign_in
from .test_storage import SEED, seed_dir  # noqa: F401  (the same two items and seed folder)
from terraforma.game import Game

pytestmark = pytest.mark.heroes


@pytest.fixture
def storage():
    return TeamStorage()


@pytest.fixture
def game(tmp_path, storage):
    return Game(name="Test Game", seed_dir=seed_dir(tmp_path), storage=storage)


@pytest.fixture
def mike(app_client):
    headers = sign_in(app_client, "Mike")
    testing.make_team(app_client, headers, "Alpha", heroes=("Aria",))
    hero = app_client.get("/api/heroes").json()[0]["id"]

    async def account(db):
        return (await db.scalar(select(Account).where(Account.username == "Mike"))).id

    return app_client, headers, hero, in_app_db(app_client, account)


def grant(client, account_id, items):
    async def work(db):
        await grants.grant(db, Rules(), account_id, 0, items, "", f"order-{sum(items.values())}-{'-'.join(items)}")

    in_app_db(client, work)


def collect(client, headers, hero, item, qty=1):
    return client.post(f"/api/heroes/{hero}/parcel/collect", json={"item": item, "qty": qty}, headers=headers)


def test_the_parcel_is_listed_and_collected_into_team_storage(mike):
    client, headers, hero, account = mike
    grant(client, account, {"potion": 3, "herb": 2})
    assert expect(client.get("/api/parcel"), 200).json() == {"parcel": {"potion": 3, "herb": 2}}
    answer = expect(collect(client, headers, hero, "potion", 2), 200).json()
    assert answer == {"taken": 2, "waiting": 1, "stored": 2, "packed": 0}
    assert expect(client.get("/api/parcel"), 200).json() == {"parcel": {"potion": 1, "herb": 2}}
    assert expect(client.get(f"/api/heroes/{hero}/storage"), 200).json()["items"][0]["qty"] == 2


def test_asking_for_more_than_waits_takes_what_is_there_and_empties_the_row(mike):
    client, headers, hero, account = mike
    grant(client, account, {"herb": 2})
    assert expect(collect(client, headers, hero, "herb", 50), 200).json()["taken"] == 2
    assert expect(client.get("/api/parcel"), 200).json() == {"parcel": {}}
    assert "none of that" in expect(collect(client, headers, hero, "herb"), 422).json()["detail"]


class OneStack(TeamStorage):
    """Room for a single stack, and no pack to spill into."""

    capacity = 1

    async def sharers(self, session, hero):
        return []


@pytest.mark.parametrize("storage", [OneStack()])
def test_what_does_not_fit_waits_in_the_parcel(mike, storage):
    client, headers, hero, account = mike
    grant(client, account, {"herb": 5, "potion": 2})
    assert expect(collect(client, headers, hero, "herb", 5), 200).json()["taken"] == 5
    answer = expect(collect(client, headers, hero, "potion", 2), 200).json()
    assert (answer["taken"], answer["waiting"]) == (0, 2), "the second stack has no room, so it stays in the parcel"
    assert expect(client.get("/api/parcel"), 200).json() == {"parcel": {"potion": 2}}


@pytest.mark.parametrize("storage", [NoStorage()])
def test_a_game_with_no_storage_puts_it_in_the_heros_pack(mike, storage):
    client, headers, hero, account = mike
    grant(client, account, {"potion": 2})
    answer = expect(collect(client, headers, hero, "potion", 2), 200).json()
    assert (answer["taken"], answer["stored"], answer["packed"]) == (2, 0, 2)
    assert look(client, hero)["items"][0]["qty"] == 2


@pytest.mark.parametrize("storage", [AccountStorage()])
def test_account_storage_keeps_it_for_all_the_players_heroes(mike, storage):
    client, headers, hero, account = mike
    grant(client, account, {"potion": 1})
    assert expect(collect(client, headers, hero, "potion"), 200).json()["stored"] == 1


def test_another_players_hero_and_an_unknown_item_are_refused(mike):
    client, headers, hero, account = mike
    grant(client, account, {"potion": 1})
    assert expect(collect(client, headers, hero + 99, "potion"), 404).status_code == 404
    assert "none of that" in expect(collect(client, headers, hero, "sword"), 422).json()["detail"]
    assert expect(client.get("/api/parcel"), 200).json() == {"parcel": {"potion": 1}}, "a refusal changes nothing"
    assert in_app_db(client, lambda db: db.scalar(select(Hero.id))) == hero
