"""What the calls depend on: a database session, the logged-in account, the caller's address."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import Economy, TeamGold
from ..alliances.hooks import Alliances
from ..relations.hooks import Relations
from ..pvp.hooks import PvpZones
from ..market.hooks import Market
from ..guild.hooks import Guild
from ..heroes.hooks import Roster
from ..npcs.hooks import Npcs
from ..npcs.inn import Inn
from ..reach.hooks import Reach
from ..towns.hooks import Towns
from ..fights.rules import Rules
from ..models import Account
from .security import SESSION_ACCOUNT, SESSION_VERSION, csrf_matches


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """A session in a transaction: committed when the call succeeds, rolled back when it fails."""
    async with request.app.state.sessionmaker() as session:
        async with session.begin():
            yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def get_rules(request: Request) -> Rules:
    """The game's fight rules (the engine's own when the app runs without a game)."""
    game = request.app.state.game
    return game.rules if game else Rules()


GameRules = Annotated[Rules, Depends(get_rules)]


def get_economy(request: Request) -> Economy:
    """The game's economy (gold on the team, when the app runs without a game)."""
    game = request.app.state.game
    return game.economy if game else TeamGold()


GameEconomy = Annotated[Economy, Depends(get_economy)]


def get_relations(request: Request) -> Relations:
    """The game's relationships rules (the engine's defaults, when the app runs without a game)."""
    game = request.app.state.game
    return game.relations if game else Relations()


GameRelations = Annotated[Relations, Depends(get_relations)]


def get_towns(request: Request) -> Towns:
    """The game's town rules (the engine's defaults, when the app runs without a game)."""
    game = request.app.state.game
    return game.towns if game else Towns()


GameTowns = Annotated[Towns, Depends(get_towns)]


def get_pvp(request: Request) -> PvpZones:
    """The game's PvP zones (the engine's default, PvP nowhere, when the app runs without a game)."""
    game = request.app.state.game
    return game.pvp if game else PvpZones()


GamePvp = Annotated[PvpZones, Depends(get_pvp)]


def get_alliances(request: Request) -> Alliances:
    """The game's alliance rules (the engine's defaults, when the app runs without a game)."""
    game = request.app.state.game
    return game.alliances if game else Alliances()


GameAlliances = Annotated[Alliances, Depends(get_alliances)]


async def current_account(request: Request, db: Db) -> Account:
    """For calls that only read: the logged-in account, or 401."""
    account_id = request.session.get(SESSION_ACCOUNT)
    account = await db.get(Account, account_id) if account_id is not None else None
    if account is None or request.session.get(SESSION_VERSION) != account.session_version:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    return account


async def acting_account(request: Request, account: Annotated[Account, Depends(current_account)]) -> Account:
    """For calls that change something: logged in, and the CSRF token matches."""
    if not csrf_matches(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or wrong CSRF token")
    return account


CurrentAccount = Annotated[Account, Depends(current_account)]
ActingAccount = Annotated[Account, Depends(acting_account)]


def client_address(request: Request) -> str:
    """The caller's IP (behind the NAS's proxy, uvicorn's --proxy-headers sets it from X-Forwarded-For)."""
    return request.client.host if request.client else "unknown"


def get_npcs(request: Request) -> Npcs:
    """The game's rules for what an NPC's dialog does with the tags the engine leaves to it."""
    game = request.app.state.game
    return game.npcs if game else Npcs()


GameNpcs = Annotated[Npcs, Depends(get_npcs)]


def get_reach(request: Request) -> Reach:
    """The game's rules for what a hero can reach (the engine's default ranges when the app runs without a game)."""
    game = request.app.state.game
    return game.reach if game else Reach()


GameReach = Annotated[Reach, Depends(get_reach)]


def get_market(request: Request) -> Market:
    """The game's shop rules (the engine's default stock and prices when the app runs without a game)."""
    game = request.app.state.game
    return game.market if game else Market()


GameMarket = Annotated[Market, Depends(get_market)]


def get_guild(request: Request) -> Guild:
    """The game's rule for who may ask to join whose party (allies only when the app runs without a game)."""
    game = request.app.state.game
    return game.guild if game else Guild()


GameGuild = Annotated[Guild, Depends(get_guild)]


def get_inn(request: Request) -> Inn:
    """The game's rule for who pays at an inn (each team pays its own when the app runs without a game)."""
    game = request.app.state.game
    return game.inn if game else Inn()


GameInn = Annotated[Inn, Depends(get_inn)]


def get_roster(request: Request) -> Roster:
    """The game's rule for changing the heroes of a saved team (no removing, replacing or moving when the app runs without a game)."""
    game = request.app.state.game
    return game.roster if game else Roster()


GameRoster = Annotated[Roster, Depends(get_roster)]
