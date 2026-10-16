"""The quest proposal calls, all for admins: look over the quests the engine made for gaps in the world's progression, approve, reject or retire
them, and run the gap check now (it also runs from housekeeping).

Each call is its own route. Calls that change something need a login and the CSRF token (``ActingAccount``) and are rate-limited per account;
the call that only reads needs a login (``CurrentAccount``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameNeeds, GameRules
from . import needs

router = APIRouter(prefix="/api/admin")

ProposalId = Annotated[int, Path(ge=1)]


def refuse(error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may look at the quests the engine made")


@router.get("/quest-proposals")
async def listing(account: CurrentAccount, db: Db, proposal_status: Annotated[str | None, Query(alias="status", pattern="^(proposed|live|rejected|retired)$")] = None,
                  after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    """The proposals, oldest first after the id $after; ``status`` narrows to one stage."""
    only_admin(account)
    return {"proposals": [needs.view(each) for each in await needs.proposals(db, proposal_status, after, limit)]}


async def change(action, proposal_id: int, request: Request, account, db) -> dict:
    only_admin(account)
    await limited(request, ratelimit.NEEDS_BY_ACCOUNT, str(account.id))
    try:
        return {"proposal": needs.view(await action(db, proposal_id))}
    except needs.NeedsError as error:
        raise refuse(error) from error


@router.post("/quest-proposals/{proposal_id}/approve")
async def approve(proposal_id: ProposalId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Makes a proposal live: a template of its slot, offered from the slot's next period."""
    return await change(needs.approve, proposal_id, request, account, db)


@router.post("/quest-proposals/{proposal_id}/reject")
async def reject(proposal_id: ProposalId, request: Request, account: ActingAccount, db: Db, hooks: GameNeeds) -> dict:
    """Turns a proposal down."""
    return await change(lambda db, each: needs.reject(db, each, hooks), proposal_id, request, account, db)


@router.post("/quest-proposals/{proposal_id}/retire")
async def retire(proposal_id: ProposalId, request: Request, account: ActingAccount, db: Db, hooks: GameNeeds) -> dict:
    """Takes a live quest out of its slot."""
    return await change(lambda db, each: needs.retire(db, each, hooks), proposal_id, request, account, db)


@router.post("/quest-needs/run")
async def run(request: Request, account: ActingAccount, db: Db, hooks: GameNeeds, rules: GameRules) -> dict:
    """Looks for gaps now and makes a quest for each (an admin only). ``proposals`` lists what was made."""
    only_admin(account)
    await limited(request, ratelimit.NEEDS_BY_ACCOUNT, str(account.id))
    return {"proposals": [needs.view(each) for each in await needs.run(db, hooks, rules)]}
