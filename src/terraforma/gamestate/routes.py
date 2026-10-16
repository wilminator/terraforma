"""The admin's planned/live state calls: look at the live and planned documents, edit a section of the draft, check it, commit it or revert it.

Each call is its own route with a strict model. The calls that change something depend on ``AdminAccount`` (login, CSRF token and
``is_admin``) and are rate-limited per account; the ones that only read need a login and ``is_admin`` (``CurrentAccount``). There is
no call that freezes the plan: an import does (slice 2).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.common import Strict, limited
from ..api.deps import AdminAccount, CurrentAccount, Db, GameStateSections, InstalledModules
from . import service, transfer
from .document import Problem

router = APIRouter(prefix="/api/admin/state")

SectionName = Annotated[str, Path(min_length=1, max_length=64)]


class EditBody(Strict):
    """A section's new value, whole. $revision, when sent, is the draft's revision the admin read: an edit made since refuses this one."""

    value: dict
    revision: int | None = Field(default=None, ge=0)


class CommitBody(Strict):
    """$hash is the draft's hash as the admin last read it (from ``GET /api/admin/state`` or the check)."""

    hash: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=200)


class ImportBody(Strict):
    """An export file as ``GET /api/admin/state/export`` wrote it. The document inside is checked against this game's sections."""

    export_format: int
    kind: str = Field(max_length=32)
    source: Literal["planned", "live"]
    hash: str = Field(min_length=64, max_length=64)
    modules: list[Annotated[str, Field(max_length=32)]] = Field(max_length=64)
    document: dict


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")


def refuse(error: service.StateError) -> HTTPException:
    if isinstance(error, service.NoSuchSection):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    detail = {"message": str(error), "problems": [problem.view() for problem in error.problems]} if isinstance(error, service.Refused) else str(error)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def snapshot_view(snapshot: service.Snapshot) -> dict:
    return {"id": snapshot.id, "hash": snapshot.hash, "committed_by": snapshot.committed_by, "committed_at": snapshot.created_at.isoformat(),
            "note": snapshot.note}


def view(current: service.State) -> dict:
    planned = current.planned
    return {"live": snapshot_view(current.live),
            "planned": {"revision": planned.revision, "hash": current.planned_hash, "frozen": planned.frozen, "differs": current.differs,
                        "edited_by": planned.edited_by, "edited_at": planned.updated_at.isoformat()}}


@router.get("")
async def overview(account: CurrentAccount, db: Db, sections: GameStateSections) -> dict:
    """Which snapshot is live and where the draft stands (its revision, hash, whether it is frozen and whether it differs from live)."""
    only_admin(account)
    return view(await service.state(db, sections))


@router.get("/live")
async def live(account: CurrentAccount, db: Db, sections: GameStateSections) -> dict:
    only_admin(account)
    snapshot = (await service.state(db, sections)).live
    return {"snapshot": snapshot_view(snapshot), "document": snapshot.document}


@router.get("/planned")
async def planned(account: CurrentAccount, db: Db, sections: GameStateSections) -> dict:
    only_admin(account)
    current = await service.state(db, sections)
    return {**view(current)["planned"], "document": current.planned.document}


@router.get("/history")
async def history(account: CurrentAccount, db: Db, sections: GameStateSections, after: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    """The committed snapshots, newest first, those before the id $after."""
    only_admin(account)
    await service.ensure(db, sections)
    return {"snapshots": [snapshot_view(snapshot) for snapshot in await service.history(db, after, limit)]}


@router.get("/export")
async def export(account: CurrentAccount, db: Db, sections: GameStateSections, modules: InstalledModules,
                 source: Literal["planned", "live"] = "planned") -> JSONResponse:
    """The planned (or, with ``source=live``, the live) state as a file to import on another server: the document, its hash and the add-ons installed here."""
    only_admin(account)
    data = await transfer.export(db, sections, modules, source)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="terraforma-state-{data["hash"][:12]}.json"'})


@router.post("/import")
async def import_state(body: ImportBody, request: Request, account: AdminAccount, db: Db, sections: GameStateSections, modules: InstalledModules) -> dict:
    """Replaces the draft with an export file's document, frozen (live is untouched until it is committed). Refused if the add-ons differ."""
    await limited(request, ratelimit.STATE_BY_ACCOUNT, str(account.id))
    try:
        return view(await transfer.import_file(db, sections, modules, body.model_dump(), account.id))
    except service.StateError as error:
        raise refuse(error) from error


@router.post("/check")
async def check(request: Request, account: AdminAccount, db: Db, sections: GameStateSections) -> dict:
    """What is wrong with the draft, in sentences, each naming its section. ``ok`` is true when it can be committed."""
    await limited(request, ratelimit.STATE_BY_ACCOUNT, str(account.id))
    current = await service.state(db, sections)
    found: list[Problem] = await service.check(db, sections)
    return {"ok": not found, "hash": current.planned_hash, "revision": current.planned.revision, "problems": [problem.view() for problem in found]}


@router.put("/planned/sections/{name}")
async def edit(name: SectionName, body: EditBody, request: Request, account: AdminAccount, db: Db, sections: GameStateSections) -> dict:
    """Replaces one section of the draft, whole. The live document is untouched, and the plan is no longer frozen."""
    await limited(request, ratelimit.STATE_BY_ACCOUNT, str(account.id))
    try:
        return view(await service.edit_section(db, sections, name, body.value, account.id, body.revision))
    except service.StateError as error:
        raise refuse(error) from error


@router.post("/revert")
async def revert(request: Request, account: AdminAccount, db: Db, sections: GameStateSections) -> dict:
    """Drops every edit: the draft becomes the live document again."""
    await limited(request, ratelimit.STATE_BY_ACCOUNT, str(account.id))
    return view(await service.revert(db, sections, account.id))


@router.post("/commit")
async def commit(body: CommitBody, request: Request, account: AdminAccount, db: Db, sections: GameStateSections) -> dict:
    """Checks the draft and, only if it is clean and is the one with $hash, makes it the live state."""
    await limited(request, ratelimit.STATE_BY_ACCOUNT, str(account.id))
    try:
        return view(await service.commit(db, sections, account.id, body.hash, body.note))
    except service.StateError as error:
        raise refuse(error) from error
