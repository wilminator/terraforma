"""The admin's calls on the living world's realms and settlements: read them, move a realm's strength, let a settlement fall or found it.

Each call is its own route. Calls that change something need a login and the CSRF token (``ActingAccount``) and are rate-limited per
account; the call that only reads needs a login (``CurrentAccount``). Only an admin may use any of them.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import Field
from sqlalchemy import select

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db
from . import service
from .models import Realm, Settlement

router = APIRouter(prefix="/api/admin")

RealmId = Annotated[int, Path(ge=1)]
SettlementId = Annotated[int, Path(ge=1)]


class NewStrength(Strict):
    strength: int = Field(ge=-1_000_000, le=1_000_000)


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may use the realms")


def refuse(error: service.RealmError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


@router.get("/realms")
async def listing(account: CurrentAccount, db: Db) -> dict:
    """Every realm and every settlement, by key."""
    only_admin(account)
    realms = (await db.scalars(select(Realm).order_by(Realm.key))).all()
    towns = (await db.scalars(select(Settlement).order_by(Settlement.key))).all()
    return {"realms": [service.realm_view(each) for each in realms], "settlements": [service.settlement_view(each) for each in towns]}


@router.post("/realms/{realm_id}/strength")
async def strength(realm_id: RealmId, body: NewStrength, request: Request, account: ActingAccount, db: Db) -> dict:
    """Sets a realm's strength (an admin stepping in on what play and time have done)."""
    only_admin(account)
    await limited(request, ratelimit.REALM_BY_ACCOUNT, str(account.id))
    try:
        return {"realm": service.realm_view(await service.set_strength(db, await service.realm(db, realm_id), body.strength))}
    except service.RealmError as error:
        raise refuse(error) from error


@router.post("/settlements/{settlement_id}/fall")
async def fall(settlement_id: SettlementId, request: Request, account: ActingAccount, db: Db) -> dict:
    """A live settlement falls: it is kept as ruins and leaves every team's visited list."""
    only_admin(account)
    await limited(request, ratelimit.REALM_BY_ACCOUNT, str(account.id))
    try:
        return {"settlement": service.settlement_view(await service.fall(db, await service.settlement(db, settlement_id)))}
    except service.RealmError as error:
        raise refuse(error) from error


@router.post("/settlements/{settlement_id}/found")
async def found(settlement_id: SettlementId, request: Request, account: ActingAccount, db: Db) -> dict:
    """A dormant settlement is founded, or one in ruins is founded again."""
    only_admin(account)
    await limited(request, ratelimit.REALM_BY_ACCOUNT, str(account.id))
    try:
        return {"settlement": service.settlement_view(await service.found(db, await service.settlement(db, settlement_id)))}
    except service.RealmError as error:
        raise refuse(error) from error
