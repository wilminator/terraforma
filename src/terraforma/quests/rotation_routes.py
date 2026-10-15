"""The rotating quest calls: a player's team looks at the quests the slots hold and asks for the one it wants, and an admin ends a slot's quest
at once when the world has changed under it.

Each call is its own route. Calls that change something need a login and the CSRF token (``ActingAccount``) and are rate-limited per account;
the call that only reads needs a login (``CurrentAccount``) and makes nothing: a slot nobody has asked about this period shows no quest yet.
Taking and finishing a quest is the game's (its dialog and dungeon use the ``slot_quest`` tag or ``quests.rotation``), so there is no call for it.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRotatingQuests
from ..heroes import service as heroes
from . import rotation
from .rotation_models import NOTHING

router = APIRouter(prefix="/api")

TeamId = Annotated[int, Path(ge=1)]
SlotKey = Annotated[str, Path(min_length=1, max_length=64)]


def refuse(error: Exception) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(error, heroes.NotFound) or "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


@router.get("/teams/{team_id}/slot-quests")
async def slot_quests(team_id: TeamId, account: CurrentAccount, db: Db) -> dict:
    """The quest each slot holds now, with the team's hold on it (``team`` is null before it asked). A slot nobody has asked about this
    period is left out: the offer call makes its quest."""
    try:
        await heroes.own_team(db, account, team_id)
    except heroes.NotFound as error:
        raise refuse(error) from error
    found = []
    for slot in await rotation.slots(db):
        quest = await rotation.peek(db, slot)
        if quest is not None and quest.source != NOTHING:
            found.append(rotation.view(quest, slot, await rotation.progress_of(db, team_id, quest)))
    return {"slots": found}


@router.post("/teams/{team_id}/slot-quests/{slot}/offer")
async def offer(team_id: TeamId, slot: SlotKey, request: Request, account: ActingAccount, db: Db, hooks: GameRotatingQuests) -> dict:
    """The team's hold on the slot's quest of the moment: the one it already has, or a new offer. ``slot`` is null when the slot holds no quest now."""
    await limited(request, ratelimit.SLOT_QUEST_BY_ACCOUNT, str(account.id))
    try:
        await heroes.own_team(db, account, team_id)
        progress = await rotation.offer(db, hooks, team_id, slot)
        if progress is None:
            return {"slot": None}
        quest = await db.get(rotation.SlotQuest, progress.slot_quest_id)
        return {"slot": rotation.view(quest, await rotation.get_slot(db, slot), progress)}
    except (heroes.NotFound, rotation.RotationError) as error:
        raise refuse(error) from error


@router.post("/admin/quest-slots/{slot}/end")
async def end(slot: SlotKey, request: Request, account: ActingAccount, db: Db, hooks: GameRotatingQuests) -> dict:
    """Ends the slot's quest now (an admin only), because the world changed under it: teams that held it are done with it and nobody is
    penalized. The next ask starts a new period with a fresh draw. ``ended`` is false when the slot held no quest."""
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may end a slot's quest")
    await limited(request, ratelimit.SLOT_QUEST_BY_ACCOUNT, str(account.id))
    try:
        quest = await rotation.end_early(db, hooks, slot)
    except rotation.RotationError as error:
        raise refuse(error) from error
    return {"ended": quest is not None}
