"""An account the sign-in provider has blocked (the shared account service's ban or suspension).

The provider's ``account-suspended`` message ``{sub, kind, until}`` blocks the person's account; ``account-reinstated`` lifts it. A block
ends by itself too: when ``until`` passes, or when the provider signs the person in with a token issued after the block began (it would
not have, were the block still on, so a lifting message must have been missed).

While blocked: every login of the account ends and none works by any method (``api.security.refuse_if_suspended``), the person's names
(handle, heroes, teams) are shown to others as the generic labels and left out of searches, the way a name that failed the screen is
(``names.display``), and they leave the monster-play queue. Nothing is deleted. Lifting puts the names back as they were. A block is
not one of the game's own bans and tells the player nothing but to sign in again.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..heroes.models import Hero, Team
from ..models import Account, BlockHidden
from ..monsterplay.models import MonsterVolunteer

KINDS = ("ban", "suspended")
#: A number this large is a Unix time in seconds; a smaller one is seconds from now (thirty years of seconds is not a ban's length).
EPOCH_SINCE = 1_000_000_000
_OWNED = (("hero", Hero), ("team", Team))


def until_of(value: object, now: datetime) -> datetime | None:
    """When a block ends, from the message's ``until``: a number of seconds (a Unix time, or from now), or nothing (it does not end)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC) if value >= EPOCH_SINCE else now + timedelta(seconds=value)
    except (OverflowError, OSError, ValueError):
        return None


async def _hide_names(db: AsyncSession, account: Account) -> None:
    """Hides the account's names from other players, remembering what each was (a name already hidden is left as it is and not remembered)."""
    kept = {(row.kind, row.row_id) for row in await db.scalars(select(BlockHidden).where(BlockHidden.account_id == account.id))}
    if account.name_ok is not False and ("account", account.id) not in kept:
        db.add(BlockHidden(account_id=account.id, kind="account", row_id=account.id, name_ok_before=account.name_ok))
        account.name_ok = False
    for kind, model in _OWNED:
        for row in await db.scalars(select(model).where(model.account_id == account.id)):
            if row.name_ok is not False and (kind, row.id) not in kept:
                db.add(BlockHidden(account_id=account.id, kind=kind, row_id=row.id, name_ok_before=row.name_ok))
                row.name_ok = False


async def _show_names(db: AsyncSession, account: Account) -> None:
    """Puts the names back as they were (a name that has been screened again meanwhile keeps its new answer)."""
    for each in list(await db.scalars(select(BlockHidden).where(BlockHidden.account_id == account.id))):
        row = account if each.kind == "account" else await db.get(Hero if each.kind == "hero" else Team, each.row_id)
        if row is not None and row.name_ok is False:
            row.name_ok = each.name_ok_before
        await db.delete(each)


async def block(db: AsyncSession, account: Account, provider: str, kind: str, until: object) -> None:
    """Blocks $account for the provider $provider. Safe to repeat: the same message again changes nothing but its end."""
    now = wallclock.now()
    if not account.is_blocked(now):
        account.suspended_at = now
    account.suspended_by = provider
    account.suspended_kind = kind if kind in KINDS else "suspended"
    account.suspended_until = until_of(until, now)
    account.session_version += 1  # every login, on every device, ends
    await _hide_names(db, account)
    await db.execute(delete(MonsterVolunteer).where(MonsterVolunteer.account_id == account.id))


async def lift(db: AsyncSession, account: Account) -> None:
    """Lifts the block and shows the names again."""
    account.suspended_by = account.suspended_kind = account.suspended_at = account.suspended_until = None
    await _show_names(db, account)


async def lift_if_ended(db: AsyncSession, account: Account) -> None:
    """Lifts a block whose end has passed (a block with no end stays)."""
    if account.suspended_by and not account.is_blocked():
        await lift(db, account)


async def lift_if_signed_in_since(db: AsyncSession, account: Account, issued_at: float | None) -> None:
    """Lifts the block when the provider signed the person in with a token issued after the block began: it does not, while the block is on."""
    began = account.suspended_at
    if account.suspended_by and issued_at is not None and began is not None:
        began = began if began.tzinfo is not None else began.replace(tzinfo=UTC)
        if issued_at > began.timestamp():
            await lift(db, account)
