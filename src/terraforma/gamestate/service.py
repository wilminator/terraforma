"""Planned and live state: the draft an admin edits, the check, the commit that makes it live, and the revert.

``live`` is the latest committed snapshot, read by the running game; ``planned`` is the one editable draft. Edits touch the draft
and nothing else. A commit runs the check and only a clean draft becomes a new snapshot. Servers share nothing in memory: the
live snapshot's id sits in one row, so a server that sees a different id than the one it holds reloads.
"""

import copy
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from . import document as documents
from .document import Problem, Section
from .models import LiveState, PlannedState, Snapshot

ROW = 1  # LiveState and PlannedState hold exactly one row, with this id.


class StateError(Exception):
    """Something an admin's request can't do; the message says what."""


class Conflict(StateError):
    """The state moved under the request (another admin edited or committed); read it again."""


class NoSuchSection(StateError):
    pass


class Refused(StateError):
    """The draft isn't fit to commit (``problems`` says why), or there is nothing to commit."""

    def __init__(self, message: str, problems: list[Problem] | None = None):
        super().__init__(message)
        self.problems = problems or []


@dataclass(frozen=True)
class Planned:
    """The draft as it was when read (plain values: a later edit does not change a copy already held)."""

    document: dict
    revision: int
    frozen: bool
    edited_by: int | None
    updated_at: datetime


@dataclass(frozen=True)
class State:
    """What an admin sees: the live snapshot (never changed once committed) and the draft, side by side."""

    live: Snapshot
    planned: Planned

    @property
    def planned_hash(self) -> str:
        return documents.hash_of(self.planned.document)

    @property
    def differs(self) -> bool:
        return self.planned_hash != self.live.hash


async def ensure(db: AsyncSession, sections: dict[str, Section]) -> None:
    """Makes the first, empty snapshot (every section at its default), live, with the draft equal to it, when the database has none.
    Safe to call from several servers at once (the pointer rows are written only if missing)."""
    if await db.get(LiveState, ROW) is not None and await db.get(PlannedState, ROW) is not None:
        return
    empty = documents.new_document(sections)
    first = Snapshot(hash=documents.hash_of(empty), document=empty, note="first state")
    db.add(first)
    await db.flush()
    await upsert(db, LiveState.__table__, {"id": ROW, "snapshot_id": first.id}, ["id"])
    await upsert(db, PlannedState.__table__, {"id": ROW, "document": empty, "revision": 0, "frozen": False}, ["id"])
    await db.flush()  # (a server that lost the race leaves an orphan snapshot; the pointer rows are the other's)


async def state(db: AsyncSession, sections: dict[str, Section]) -> State:
    await ensure(db, sections)
    live_id = await db.scalar(select(LiveState.snapshot_id).where(LiveState.id == ROW))
    row = await db.get(PlannedState, ROW, populate_existing=True)
    planned = Planned(copy.deepcopy(row.document), row.revision, row.frozen, row.edited_by, row.updated_at)
    return State(live=await db.get(Snapshot, live_id), planned=planned)


async def live_snapshot(db: AsyncSession, sections: dict[str, Section]) -> Snapshot:
    """What the running game reads: the latest committed snapshot (never the draft)."""
    return (await state(db, sections)).live


async def check(db: AsyncSession, sections: dict[str, Section]) -> list[Problem]:
    """What is wrong with the draft (an empty list: it can be committed)."""
    return documents.problems_in((await state(db, sections)).planned.document, sections)


async def _write_planned(db: AsyncSession, seen: int, document: dict, frozen: bool, account_id: int | None) -> None:
    """Replaces the draft only if it is still at revision $seen (compare and set), so a concurrent edit is refused, not lost."""
    changed = await db.execute(
        update(PlannedState).where(PlannedState.id == ROW, PlannedState.revision == seen)
        .values(document=document, revision=seen + 1, frozen=frozen, edited_by=account_id).execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise Conflict("the planned state changed while you were editing it; read it again")


async def edit_section(db: AsyncSession, sections: dict[str, Section], name: str, value: dict, account_id: int | None,
                       revision: int | None = None) -> State:
    """Replaces one section of the draft whole (the draft is not checked until a commit or a check call). Clears the frozen flag.
    $revision, when given, must be the draft's current one."""
    if name not in sections:
        raise NoSuchSection(f"there is no section called {name!r} in this game")
    current = await state(db, sections)
    if revision is not None and revision != current.planned.revision:
        raise Conflict("the planned state changed since you read it; read it again")
    document = {**current.planned.document, "sections": {**current.planned.document["sections"], name: value}}
    try:
        size = len(documents.text_of(document).encode())
    except ValueError as error:
        raise StateError(str(error)) from error
    if size > documents.MAX_BYTES:
        raise StateError(f"that makes the document {size} bytes; the most allowed is {documents.MAX_BYTES}")
    await _write_planned(db, current.planned.revision, document, False, account_id)
    return await state(db, sections)


async def revert(db: AsyncSession, sections: dict[str, Section], account_id: int | None) -> State:
    """Replaces the draft with the live document (every unfinished edit is dropped; nothing is left to freeze)."""
    current = await state(db, sections)
    await _write_planned(db, current.planned.revision, current.live.document, False, account_id)
    return await state(db, sections)


async def freeze(db: AsyncSession, sections: dict[str, Section], document: dict, account_id: int | None) -> State:
    """Replaces the draft with $document and marks it frozen. For an import (slice 2) only: there is no call for it, because
    nobody freezes a plan by hand. Raises Refused when $document does not check out."""
    found = documents.problems_in(document, sections)
    if found:
        raise Refused("that document can't be used", found)
    current = await state(db, sections)
    await _write_planned(db, current.planned.revision, document, True, account_id)
    return await state(db, sections)


async def commit(db: AsyncSession, sections: dict[str, Section], account_id: int | None, expected_hash: str, note: str = "") -> State:
    """Makes the draft live, if it checks out. $expected_hash is the hash of the draft the admin looked at: if the draft has
    changed since, nothing is committed. A draft equal to the live document has nothing to commit."""
    current = await state(db, sections)
    if current.planned_hash != expected_hash:
        raise Conflict("the planned state is not the one you checked; read it again")
    found = documents.problems_in(current.planned.document, sections)
    if found:
        raise Refused(f"the planned state has {len(found)} problem{'s' if len(found) != 1 else ''}; nothing was committed", found)
    if not current.differs:
        raise Refused("the planned state is the same as the live one; there is nothing to commit")
    snapshot = Snapshot(hash=current.planned_hash, document=current.planned.document, committed_by=account_id, note=note)
    db.add(snapshot)
    await db.flush()
    moved = await db.execute(
        update(LiveState).where(LiveState.id == ROW, LiveState.snapshot_id == current.live.id).values(snapshot_id=snapshot.id)
        .execution_options(synchronize_session=False)
    )
    if moved.rowcount != 1:
        raise Conflict("another commit went live first; read the state again")
    await _apply(db, sections, snapshot.document)
    # The draft is what is live now, so nothing is left to stage.
    await db.execute(update(PlannedState).where(PlannedState.id == ROW).values(frozen=False).execution_options(synchronize_session=False))
    return await state(db, sections)


async def _apply(db: AsyncSession, sections: dict[str, Section], document: dict) -> None:
    for name, section in sorted(sections.items()):
        await section.apply(db, document["sections"].get(name, section.default()))


async def apply_live(db: AsyncSession, sections: dict[str, Section]) -> None:
    """Has every section make the database match the live snapshot: for the start of a server, so rows a section keeps in step
    with the live state are right whatever happened while it was down (the first state is made if there is none)."""
    await _apply(db, sections, (await state(db, sections)).live.document)


async def history(db: AsyncSession, after: int = 0, limit: int = 50) -> list[Snapshot]:
    """Committed snapshots, newest first, those with an id below $after (0: from the newest)."""
    query = select(Snapshot).order_by(Snapshot.id.desc()).limit(limit)
    if after:
        query = query.where(Snapshot.id < after)
    return list((await db.scalars(query)).all())
