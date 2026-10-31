"""The escort contract's bookkeeping: where the escorted party is, who may leave it, and where it may warp.

An escort (the ``escort_to`` term of ``contracts.terms``) hires one team to take the other team to a place and keep it alive. The escorted
team joins the hired team's party when the contract is accepted (``service.accept``). From then on, while the contract is active:

* neither team can leave the party (``locked``), so nobody can leave to void the contract or push the other out of it;
* a warp by their party can only go to a place both teams have visited (``restricted``);
* every step or warp the party takes is noted (``note``): reaching the destination is recorded on the term, because a tile is only stood on
  for a moment and the contract may be judged long after. A dungeon destination is reached by being inside an instance on that map, and
  is done once the party is back where it started (``origin``, recorded when the escort began).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Map
from .models import ACTIVE, PENDING, Contract, ContractTerm

ESCORT = "escort_to"


async def _active_escorts(session: AsyncSession, team_ids) -> list[tuple[Contract, ContractTerm]]:
    """The pending escort terms of active contracts that one of the teams is on a side of."""
    ids = list(team_ids)
    if not ids:
        return []
    rows = await session.execute(
        select(Contract, ContractTerm).join(ContractTerm, ContractTerm.contract_id == Contract.id)
        .where(Contract.state == ACTIVE, ContractTerm.kind == ESCORT, ContractTerm.state == PENDING,
               (Contract.a_team_id.in_(ids)) | (Contract.b_team_id.in_(ids)))
    )
    return [(contract, term) for contract, term in rows.all()]


async def locked(session: AsyncSession, team_id: int) -> bool:
    """Whether the team is on either side of a running escort, and so stays in its party until the contract ends."""
    return bool(await _active_escorts(session, [team_id]))


async def restricted(session: AsyncSession, team_ids) -> bool:
    """Whether a party with these teams may warp only to places every team in it has visited."""
    return bool(await _active_escorts(session, team_ids))


def protector_of(contract: Contract, term: ContractTerm) -> int:
    return contract.a_team_id if term.side == "a" else contract.b_team_id


def protected_of(contract: Contract, term: ContractTerm) -> int:
    return contract.b_team_id if term.side == "a" else contract.a_team_id


async def note(session: AsyncSession, party) -> None:
    """The party has just arrived at its tile (a step or a warp): records on any escort it is part of that it reached the destination, or,
    for a dungeon, that it went in and now is back at its origin. Does nothing for a party in no escort."""
    from ..instances import service as instances  # (here, since instances reads the maps)
    from ..parties import service as parties

    teams = await parties.team_ids(session, party.id)
    for contract, term in await _active_escorts(session, teams):
        if not {protector_of(contract, term), protected_of(contract, term)} <= set(teams):
            continue  # the pair is not together in this party
        params = dict(term.params)
        destination = await session.scalar(select(Map.id).where(Map.name == params["to_map"], Map.world_id == (await session.get(Map, party.map_id)).world_id))
        if destination is None:
            continue
        if params.get("dungeon"):
            if not params.get("reached"):
                inside = await instances.instance_of(session, party.id)
                if inside is not None and inside.map_id == destination:
                    params["reached"] = True
            elif params.get("origin") and [party.map_id, party.x, party.y] == params["origin"]:
                params["done"] = True
        elif party.map_id == destination and ("x" not in params or (party.x, party.y) == (params["x"], params["y"])):
            params["done"] = True
        if params != term.params:
            term.params = params
    await session.flush()
