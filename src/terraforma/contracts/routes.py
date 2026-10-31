"""The contract calls: offer, accept, decline, withdraw, and see the team's contracts. Each is its own route."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field
from sqlalchemy import or_, select

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, GameContracts, GameEconomy, GameFees, GameRules, GameStorage
from ..heroes import service
from ..heroes.models import Hero, Team, TeamMember
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import bounties
from . import service as contracts_service
from .models import Bounty, Contract

router = APIRouter(prefix="/api/heroes/{hero_id}")


class Term(Strict):
    kind: str = Field(min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict, max_length=16)


class Offer(Strict):
    guild: str = Field(min_length=1, max_length=64)
    to_team_id: int = Field(ge=1)
    my_terms: list[Term] = Field(default_factory=list, max_length=contracts_service.MAX_TERMS)
    their_terms: list[Term] = Field(default_factory=list, max_length=contracts_service.MAX_TERMS)
    my_escrow: int = Field(default=0, ge=0, le=contracts_service.MAX_AMOUNT)
    their_escrow: int = Field(default=0, ge=0, le=contracts_service.MAX_AMOUNT)
    duration: int = Field(ge=1, le=contracts_service.MAX_DURATION)
    payment: int = Field(default=0, ge=0, le=contracts_service.MAX_AMOUNT)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, contracts_service.ContractError):
        gone = any(words in str(error) for words in ("no such", "gone", "not for your team"))
        return HTTPException(status.HTTP_404_NOT_FOUND if gone else status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


async def _team_id(db, hero: Hero) -> int | None:
    return await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))


async def _view(db, contract: Contract) -> dict:
    names = dict((await db.execute(select(Team.id, Team.name).where(Team.id.in_([t for t in (contract.a_team_id, contract.b_team_id) if t is not None])))).all())
    return {
        "id": contract.id, "guild": contract.guild, "state": contract.state, "open": contract.a_team_id is None, "reward": contract.reward_gold, "payment": contract.payment_gold,
        "a": {"team_id": contract.a_team_id, "team": names.get(contract.a_team_id), "escrow": contract.a_escrow_gold},
        "b": {"team_id": contract.b_team_id, "team": names.get(contract.b_team_id), "escrow": contract.b_escrow_gold},
        "terms": [{"side": t.side, "kind": t.kind, "params": t.params, "state": t.state} for t in await contracts_service.terms_of(db, contract)],
        "duration": contract.duration, "offer_expires_at": contract.offer_expires_at, "starts_at": contract.starts_at,
        "ends_at": contract.ends_at, "breached_by": contract.breached_by,
    }


@router.post("/contracts")
async def offer_contract(
    hero_id: Id, body: Offer, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, fees: GameFees, contracts: GameContracts,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        contract = await contracts_service.offer(
            db, economy, fees, contracts, hero, body.guild, body.to_team_id,
            [(t.kind, t.params) for t in body.my_terms], [(t.kind, t.params) for t in body.their_terms],
            body.my_escrow, body.their_escrow, body.duration, body.payment,
        )
        return await _view(db, contract)
    except (service.HeroError, contracts_service.ContractError) as error:
        raise refuse(error) from error


@router.post("/contracts/{contract_id}/accept")
async def accept_contract(
    hero_id: Id, contract_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, fees: GameFees, contracts: GameContracts,
    rules: GameRules,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await _view(db, await contracts_service.accept(db, economy, fees, contracts, hero, contract_id, rules.party_size))
    except (service.HeroError, contracts_service.ContractError) as error:
        raise refuse(error) from error


@router.post("/contracts/{contract_id}/enter-instance")
async def enter_contract_instance(hero_id: Id, contract_id: Id, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await _view(db, await contracts_service.enter_instance(db, hero, contract_id))
    except (service.HeroError, contracts_service.ContractError) as error:
        raise refuse(error) from error


@router.post("/contracts/{contract_id}/decline")
async def decline_contract(
    hero_id: Id, contract_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, storage: GameStorage,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await _view(db, await contracts_service.decline(db, economy, storage, hero, contract_id))
    except (service.HeroError, contracts_service.ContractError) as error:
        raise refuse(error) from error


@router.post("/contracts/{contract_id}/withdraw")
async def withdraw_contract(
    hero_id: Id, contract_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, storage: GameStorage,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await _view(db, await contracts_service.withdraw(db, economy, storage, hero, contract_id))
    except (service.HeroError, contracts_service.ContractError) as error:
        raise refuse(error) from error


@router.get("/contracts")
async def my_contracts(hero_id: Id, account: CurrentAccount, db: Db, limit: int = 50) -> list[dict]:
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    team_id = await _team_id(db, hero)
    if team_id is None:
        return []
    rows = await db.scalars(
        select(Contract).where(or_(Contract.a_team_id == team_id, Contract.b_team_id == team_id))
        .order_by(Contract.id.desc()).limit(max(1, min(limit, 100)))
    )
    return [await _view(db, contract) for contract in rows.all()]


@router.get("/open-contracts")
async def open_contracts(hero_id: Id, account: CurrentAccount, db: Db, guild: str | None = Query(default=None, max_length=64), limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    try:
        await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return [await _view(db, contract) for contract in await contracts_service.open_offers(db, guild, limit)]


@router.get("/contracts/{contract_id}")
async def one_contract(hero_id: Id, contract_id: Id, account: CurrentAccount, db: Db) -> dict:
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    contract = await db.get(Contract, contract_id)
    team_id = await _team_id(db, hero)
    if contract is None or team_id not in (contract.a_team_id, contract.b_team_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there is no such contract of yours")
    return await _view(db, contract)


class PostBounty(Strict):
    guild: str = Field(min_length=1, max_length=64)
    target_team_id: int = Field(ge=1)
    scope: str = Field(pattern="^(team|hero)$")
    hero_id: int | None = Field(default=None, ge=1)
    reward: int = Field(ge=1, le=bounties.MAX_AMOUNT)
    lifetime: int = Field(ge=1, le=bounties.MAX_LIFETIME)
    alliance_id: int | None = Field(default=None, ge=1)


def _bounty_view(bounty: Bounty) -> dict:
    return {
        "id": bounty.id, "guild": bounty.guild, "poster_team_id": bounty.poster_team_id, "alliance_id": bounty.alliance_id,
        "target_team_id": bounty.target_team_id, "scope": bounty.scope, "hero_id": bounty.hero_id, "reward": bounty.reward_gold,
        "state": bounty.state, "expires_at": bounty.expires_at, "paid_to": bounty.paid_to,
    }


def refuse_bounty(error: ValueError) -> HTTPException:
    if isinstance(error, bounties.BountyError):
        return HTTPException(status.HTTP_404_NOT_FOUND if any(words in str(error) for words in ("no such bounty", "bounty of yours")) else status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.post("/bounties")
async def post_bounty(
    hero_id: Id, body: PostBounty, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, alliances_hooks: GameAlliances,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        posted = await bounties.post(
            db, economy, hero, body.guild, body.target_team_id, body.scope, body.hero_id, body.reward, body.lifetime, alliances_hooks, body.alliance_id,
        )
        return _bounty_view(posted)
    except (service.HeroError, bounties.BountyError) as error:
        raise refuse_bounty(error) from error


@router.post("/bounties/{bounty_id}/withdraw")
async def withdraw_bounty(
    hero_id: Id, bounty_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, storage: GameStorage,
) -> dict:
    await limited(request, ratelimit.CONTRACT_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return _bounty_view(await bounties.withdraw(db, economy, storage, hero, bounty_id))
    except (service.HeroError, bounties.BountyError) as error:
        raise refuse_bounty(error) from error


@router.get("/bounties")
async def open_bounties(hero_id: Id, account: CurrentAccount, db: Db, guild: str | None = Query(default=None, max_length=64), limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    try:
        await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse_hero(error) from error
    return [_bounty_view(each) for each in await bounties.open_bounties(db, guild, limit)]
