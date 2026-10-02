"""Forming, changing and reading relationships, through the game's ``Relations``.

Service functions, not calls: the calls (``relations.routes``) and a game's own rules both use them, so what may
change is the game's decision (``Relations.resolve``, ``Relations.may_form``) either way. They raise ``RelationError``
(a message the player can read); the caller's transaction rolls back.
"""

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..alliances.models import Alliance
from ..heroes.models import Team
from .hooks import Change, Ref, Relations
from .models import NOTE_MAX, RatingPrompt, Relationship

#: The kinds of side there are.
KINDS = ("team", "alliance")


class RelationError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(RelationError):
    pass


async def name_of(session: AsyncSession, ref: Ref) -> str | None:
    """The side's name, or None if there is no such side."""
    if ref.kind == "team":
        team = await session.get(Team, ref.id)
        return team.name if team else None
    if ref.kind == "alliance":
        alliance = await session.get(Alliance, ref.id)
        return alliance.name if alliance else None
    return None


async def check_sides(session: AsyncSession, subject: Ref, object: Ref) -> None:
    if subject == object:
        raise RelationError(f"a {subject.kind} has no relationship with itself")
    if await name_of(session, subject) is None:
        raise NotFound(f"there's no such {subject.kind}")
    if await name_of(session, object) is None:
        raise NotFound(f"there's no such {object.kind}")


async def get(session: AsyncSession, subject: Ref, object: Ref, *, lock: bool = False) -> Relationship | None:
    query = select(Relationship).where(
        Relationship.subject_kind == subject.kind, Relationship.subject_id == subject.id,
        Relationship.object_kind == object.kind, Relationship.object_id == object.id,
    )
    return await session.scalar(query.with_for_update() if lock else query)


async def _form(session: AsyncSession, relations: Relations, subject: Ref, object: Ref, change: Change) -> Relationship:
    if not await relations.may_form(session, subject, object, change):
        raise RelationError("these two can't form a relationship")
    row = Relationship(
        subject_kind=subject.kind, subject_id=subject.id, object_kind=object.kind, object_id=object.id,
        score=await relations.initial(session, subject, object), note="",
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # someone formed it at the same instant: use theirs
        return await get(session, subject, object, lock=True)
    return row


async def apply(session: AsyncSession, relations: Relations, change: Change) -> Relationship:
    """Applies a change to ``change.subject``'s view of ``change.object``, forming the relationship if it is new.
    The score that results is the game's (``Relations.resolve``)."""
    if change.score is None and change.delta is None:
        raise RelationError("say a score or a change")
    subject, object = change.subject, change.object
    await check_sides(session, subject, object)
    row = await get(session, subject, object, lock=True) or await _form(session, relations, subject, object, change)
    row.score = await relations.resolve(session, subject, object, row.score, change)
    await session.flush()
    return row


async def set_note(session: AsyncSession, relations: Relations, subject: Ref, object: Ref, note: str) -> Relationship:
    """Sets the subject's private note about the object (forming the relationship, at its starting score, if it is new)."""
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise RelationError(f"a note is at most {NOTE_MAX} characters")
    await check_sides(session, subject, object)
    row = await get(session, subject, object, lock=True) or await _form(session, relations, subject, object, Change(subject, object, delta=0))
    row.note = note
    await session.flush()
    return row


async def forget(session: AsyncSession, ref: Ref) -> None:
    """Removes every relationship the side has or is the object of (when it goes)."""
    await session.execute(delete(Relationship).where(or_(
        (Relationship.subject_kind == ref.kind) & (Relationship.subject_id == ref.id),
        (Relationship.object_kind == ref.kind) & (Relationship.object_id == ref.id),
    )))
    if ref.kind == "team":
        await session.execute(delete(RatingPrompt).where(or_(RatingPrompt.subject_team_id == ref.id, RatingPrompt.object_team_id == ref.id)))


async def drop(session: AsyncSession, subject: Ref, object: Ref) -> bool:
    """The subject lets go of its relationship with the object. True if there was one."""
    row = await get(session, subject, object, lock=True)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def view(session: AsyncSession, relations: Relations, row: Relationship) -> dict:
    other = Ref(row.object_kind, row.object_id)
    return {
        "kind": other.kind, "id": other.id, "name": await name_of(session, other),
        "score": row.score, "band": relations.band(row.score), "note": row.note,
    }


async def list_for(session: AsyncSession, relations: Relations, subject: Ref) -> list[dict]:
    """The subject's own relationships, by name."""
    rows = (await session.scalars(select(Relationship).where(
        Relationship.subject_kind == subject.kind, Relationship.subject_id == subject.id
    ).order_by(Relationship.id))).all()
    views = [await view(session, relations, row) for row in rows]
    return sorted(views, key=lambda each: (each["name"] or "", each["id"]))
