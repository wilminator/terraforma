"""The simulated world: a game's real app, running in this process on a scratch SQLite database, driven the way a server and its players drive it.

* The **players** sign up, log in, make teams and shop over the game's real HTTP routes (a login cookie and the CSRF token, like a browser).
* The **admin** uses the real admin routes (``/api/admin/realms`` and the rest) to look at the world and to stir it (a raid is a supply cut).
* **Time** is the world clock (``World.tick``), moved on by ``advance`` the way the real clock moves, and **housekeeping** runs as the server
  runs it (``housekeeping.run``: strength drifts, settlements fall or are founded, supply quests are made and taken back).
* What a dialog would do when a team finishes a supply quest (hands the goods in) is ``quests.needs.deliver``: ``deliver`` calls that.

Nothing here reaches into a table to change the world except to set the stage (an admin account, a visited-town entry, a team's gold),
and each of those says so. The expected values in the scenarios come from the game's own hooks and from plain arithmetic, never from the
code under test.
"""

import asyncio
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import select

from ...accounts import service as accounts_service
from ...accounts.service import create_account
from ...challenge import service as challenge
from ...db.schema import ensure_schema
from ...db.session import make_engine
from ...game import Game
from ...heroes.models import Hero
from ...keys import new_key
from ...models import World, advance_clock
from ...realms import supply
from ...realms.models import Material, Realm, Settlement, SettlementSupply
from ...towns.models import VisitedTown
from ...world.start import ensure_start

PASSWORD = "correct horse battery"
SECRET = "x" * 32


@dataclass
class Player:
    """A simulated person: an account, and once ``team`` is called the team and its first hero."""

    name: str
    admin: bool = False
    csrf: str = ""
    team_id: int = 0
    hero_id: int = 0
    cookies: list | None = None


class Sim:
    def __init__(self, client, game: Game, settings):
        self.client, self.game, self.settings = client, game, settings
        self.players: dict[str, Player] = {}
        self.current: str | None = None
        self.world_id = self.run(_world_id)

    # -- the database, from the app's own event loop --------------------------------------------------------------

    def run(self, work):
        """Runs async $work(session) on the app's database in one committed transaction, and returns what it returns."""

        async def go():
            async with self.client.app.state.sessionmaker() as session:
                async with session.begin():
                    return await work(session)

        return self.client.portal.call(go)

    # -- time -----------------------------------------------------------------------------------------------------

    @property
    def tick(self) -> int:
        async def now(db):
            return (await db.get(World, self.world_id)).tick

        return self.run(now)

    def advance(self, ticks: int) -> int:
        """Lets $ticks of game time pass (no housekeeping runs by itself: call ``housekeeping``). Returns the new tick."""
        return self.run(lambda db: advance_clock(db, self.world_id, ticks))

    def housekeeping(self) -> dict[str, int]:
        """One housekeeping pass, exactly as the server runs it. Returns what each job did (``realm_strength``: settlements fallen or
        founded; ``quest_needs``: proposals made)."""
        from ... import housekeeping

        game = self.game

        async def go():
            return await housekeeping.run(self.client.app.state.sessionmaker, self.settings, game.rules, game.needs, game)

        return self.client.portal.call(go)

    def pass_through(self, ticks: int, hop: int) -> int:
        """Advances $ticks in hops of $hop, running housekeeping after each, as a server that runs all that time would. Returns passes run."""
        done = passes = 0
        while done < ticks:
            step = min(hop, ticks - done)
            self.advance(step)
            self.housekeeping()
            done, passes = done + step, passes + 1
        return passes

    # -- people over HTTP ------------------------------------------------------------------------------------------

    def player(self, name: str, admin: bool = False) -> Player:
        """A new account, logged in (the account is made directly, as a confirmed email would make it; everything after is over HTTP)."""
        if name in self.players:
            return self.players[name]

        async def make(db):
            account = await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
            if admin:
                await challenge.set_admin(db, account.id)

        self.run(make)
        self.players[name] = Player(name, admin)
        self.login(name)
        return self.players[name]

    def login(self, name: str) -> dict[str, str]:
        """Makes $name the one the client is logged in as (a second browser, in effect); returns the headers a changing call needs."""
        who = self.players[name]
        if self.current != name:
            if who.cookies is None:  # (logging in is rate limited, as it should be: a player logs in once and keeps their cookies)
                self.client.cookies.clear()
                answer = self.client.post("/api/login", json={"username": name, "password": PASSWORD})
                assert answer.status_code == 200, answer.text
                who.csrf, who.cookies = answer.json()["csrf_token"], list(self.client.cookies.jar)
            self.client.cookies.clear()
            for cookie in who.cookies:
                self.client.cookies.jar.set_cookie(cookie)
            self.current = name
        return {"X-CSRF-Token": who.csrf}

    def call(self, name: str, method: str, path: str, expect: int | tuple[int, ...] = 200, /, **body):
        """A call as $name. Fails loudly on an unexpected status (a simulator bug or a broken route, not a failed check)."""
        headers = self.login(name)
        answer = self.client.request(method, path, headers=headers, **({"json": body} if body else {}))
        allowed = (expect,) if isinstance(expect, int) else expect
        if answer.status_code not in allowed:
            raise AssertionError(f"{method} {path} as {name}: {answer.status_code} {answer.text[:300]}")
        return answer

    def team(self, name: str, gold: int = 0, job: str = "fighter") -> Player:
        """$name makes a team of one hero over HTTP (the team gets $gold, set directly: a quest's pay would do it)."""
        who = self.players[name]
        made = self.call(name, "POST", "/api/teams", 201, name=f"{name} team"[:24], heroes=[{"name": f"{name} hero"[:24], "job": job}]).json()
        who.team_id = made["id"]
        who.hero_id = next(each for each in self.call(name, "GET", "/api/heroes").json() if each["name"] == f"{name} hero"[:24])["id"]
        if gold:
            self.set_gold(who, gold)
        return who

    def set_gold(self, who: Player, gold: int) -> None:
        from ...heroes.models import Team

        async def go(db):
            (await db.get(Team, who.team_id)).gold = gold

        self.run(go)

    def gold(self, who: Player) -> int:
        from ...heroes.models import Team

        return self.run(lambda db: _gold(db, who.team_id, Team))

    # -- the admin's view and hand -------------------------------------------------------------------------------

    def admin(self) -> Player:
        return self.players.get("Overseer") or self.player("Overseer", admin=True)

    def world(self) -> dict:
        """``GET /api/admin/realms``: every realm and settlement, as the admin sees them."""
        return self.call(self.admin().name, "GET", "/api/admin/realms").json()

    def realm(self, key: str) -> dict:
        return next(each for each in self.world()["realms"] if each["key"] == key)

    def town(self, key: str) -> dict:
        return next(each for each in self.world()["settlements"] if each["key"] == key)

    def supply(self, town_key: str, material: str) -> dict:
        """``GET /api/admin/settlements/{id}/supply`` for one material: material, level, capacity, rate, drain, cut."""
        town = self.town(town_key)
        found = self.call(self.admin().name, "GET", f"/api/admin/settlements/{town['id']}/supply").json()["supplies"]
        return next(each for each in found if each["material"] == material)

    def supplies(self, town_key: str) -> list[dict]:
        return self.call(self.admin().name, "GET", f"/api/admin/settlements/{self.town(town_key)['id']}/supply").json()["supplies"]

    def cut(self, town_key: str, material: str, percent: int) -> dict:
        """The admin (or an event) cuts a town's own sources of a material; 100 stops them, 0 restores them."""
        town = self.town(town_key)
        return self.call(self.admin().name, "POST", f"/api/admin/settlements/{town['id']}/supply/{material}/cut", cut=percent).json()["supply"]

    def set_strength(self, realm_key: str, strength: int) -> dict:
        return self.call(self.admin().name, "POST", f"/api/admin/realms/{self.realm(realm_key)['id']}/strength", strength=strength).json()["realm"]

    def fall(self, town_key: str) -> dict:
        return self.call(self.admin().name, "POST", f"/api/admin/settlements/{self.town(town_key)['id']}/fall").json()["settlement"]

    def found(self, town_key: str) -> dict:
        return self.call(self.admin().name, "POST", f"/api/admin/settlements/{self.town(town_key)['id']}/found").json()["settlement"]

    # -- play that has no route of its own yet ------------------------------------------------------------------

    def visit(self, who: Player, town_key: str) -> None:
        """The team has been to the town (the row a party's coming into a town leaves: the team, the town's map and its key)."""
        async def go(db):
            town = await db.scalar(select(Settlement).where(Settlement.world_id == self.world_id, Settlement.key == town_key))
            db.add(VisitedTown(team_id=who.team_id, map_id=town.map_id, town=town.key, x=town.x or 0, y=town.y or 0))

        self.run(go)

    def visited(self, who: Player) -> list[str]:
        async def go(db):
            return sorted((await db.scalars(select(VisitedTown.town).where(VisitedTown.team_id == who.team_id))).all())

        return self.run(go)

    def nudge(self, realm_key: str, delta: int) -> int:
        """What a dialog's ``realm_nudge`` does after a quest outcome: the realm's strength moves by $delta. Returns the new strength."""
        from ...realms import service

        async def go(db):
            return (await service.nudge_strength(db, await service.realm_by_key(db, self.world_id, realm_key), delta)).strength

        return self.run(go)

    def deliver(self, proposal_spec: dict, amount: int | None = None) -> int:
        """A team finishes a supply quest and hands the goods in (what the game's dialog does with the quest's ``spec``): how much fitted."""
        from ...quests import needs

        return self.run(lambda db: needs.deliver(db, proposal_spec, amount))

    def proposals(self, status: str | None = None) -> list[dict]:
        from ...quests import needs

        async def go(db):
            return [needs.view(each) for each in await needs.proposals(db, status, limit=500)]

        return self.run(go)

    def approve(self, proposal_id: int) -> dict:
        """An admin approves a waiting proposal."""
        from ...quests import needs

        async def go(db):
            return needs.view(await needs.approve(db, proposal_id))

        return self.run(go)

    # -- looking --------------------------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        """The world in numbers, read at the world clock (nothing is changed): realm strengths, settlement states, supply fullness in percent."""
        async def go(db):
            now = (await db.get(World, self.world_id)).tick
            realms = {each.key: each.strength for each in (await db.scalars(select(Realm).where(Realm.world_id == self.world_id))).all()}
            towns = {each.key: each.state for each in (await db.scalars(select(Settlement).where(Settlement.world_id == self.world_id))).all()}
            fills = {}
            for town, material, row in (await db.execute(
                select(Settlement, Material, SettlementSupply).join(SettlementSupply, SettlementSupply.settlement_id == Settlement.id)
                .join(Material, Material.id == SettlementSupply.material_id).where(Settlement.world_id == self.world_id))).all():
                fills[(town.key, material.key)] = supply.level_at(row, now) * 100 // row.capacity if row.capacity else 0
            return {"tick": now, "strength": realms, "state": towns, "fullness": fills}

        return self.run(go)

    def shop(self, who: Player, shop_key: str, dialog: str | None = None) -> dict:
        """The player stands beside a keeper whose dialog opens ``shop,<shop_key>`` and looks at the wares over HTTP: ``{item: price}``."""
        from ...npcs import service as npcs

        text = dialog or f"Welcome.`shop,{shop_key},Bye`\nWell?`ack`Done.`jump,end`" "`label,Bye`Bye."

        async def stand(db):
            hero = await db.get(Hero, who.hero_id)
            keeper = await npcs.place_npc(db, f"keeper-{shop_key}", "Keeper", hero.map_id, hero.x + 1, hero.y, text)
            await npcs.talk(db, self.game.npcs, self.game.reach, hero, keeper.id)

        self.run(stand)
        return {ware["item"]: ware["price"] for ware in self.call(who.name, "GET", f"/api/heroes/{who.hero_id}/dialog/shop").json()["wares"]}

    def buy(self, who: Player, item: str, qty: int = 1, expect: int | tuple[int, ...] = 200):
        return self.call(who.name, "POST", f"/api/heroes/{who.hero_id}/dialog/shop/buy", expect, item=item, qty=qty)


async def _world_id(db) -> int:
    return (await ensure_start(db)).world_id


async def _make_tables(url: str) -> None:
    engine = make_engine(url)
    try:
        await ensure_schema(engine)
    finally:
        await engine.dispose()


async def _gold(db, team_id, Team):
    return (await db.get(Team, team_id)).gold


@contextmanager
def boot(game: Game, folder: Path | None = None) -> Iterator[Sim]:
    """The game's app, running on a fresh SQLite database in a scratch folder (removed afterwards unless $folder is given)."""
    from starlette.testclient import TestClient

    from ...app import create_app
    from ...settings import Settings

    cheap = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)  # (same algorithm; the work factor is what costs, and no scenario depends on it)
    keep = (accounts_service._hasher, accounts_service._DUMMY_HASH)
    accounts_service._hasher, accounts_service._DUMMY_HASH = cheap, cheap.hash("not a real password")
    scratch = tempfile.TemporaryDirectory() if folder is None else None
    root = Path(scratch.name if scratch else folder)
    root.mkdir(parents=True, exist_ok=True)
    try:
        new_key(root / "keys")
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{root / 'world.db'}", session_secret=SECRET, secure_cookies=False, public_url="http://game.test",
            key_dir=root / "keys", fight_timer_seconds=0, housekeeping_seconds=0,  # (the scenarios run housekeeping themselves, on the clock they move)
        )
        asyncio.run(_make_tables(settings.database_url))
        with TestClient(create_app(settings, game)) as client:
            yield Sim(client, game, settings)
    finally:
        accounts_service._hasher, accounts_service._DUMMY_HASH = keep
        if scratch:
            scratch.cleanup()
