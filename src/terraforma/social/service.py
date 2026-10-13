"""Following, profile bans and the directory.

A **follow** is one way, instant and private to the follower; the followed player only sees how many follow them. The follower may,
follow by follow, switch on ``share_back``: the followed player then sees their handle and profile link (switching it off, or being
banned, takes it back). Following is capped by ``Rules.follow_cap`` (10 and one more for each hour played to begin with).

A **ban** bars a player from the banner's profile pages and nothing else: it does nothing in a game. It overrides a follow of the
banner by the banned player (the follow stays on the banned player's own list, so the ban is not given away, but it counts for
nothing: not in the banner's follower count, never shared back) and takes the share-back away.

The **directory** is a search by the start of a handle among the players who opted in (``PlayerProfile.directory``). A result shows the
handle and bio, whether the searcher follows them, and the profile link only if that player switched it on (``directory_link``).
A player who banned the searcher is not in their results.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..accounts.service import handle_key
from ..fights.rules import Rules
from ..models import Account
from ..playtime import service as playtime
from ..playtime.models import PlayTime
from ..profiles.models import PlayerProfile
from .models import Follow, ProfileBan

RESULTS = 20


class SocialError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(SocialError):
    pass


async def account_named(session: AsyncSession, handle: str) -> Account:
    """The player with this public handle (case and spacing aside)."""
    account = await session.scalar(select(Account).where(Account.handle_key == handle_key(handle)))
    if account is None:
        raise NotFound("there's no player with that handle")
    return account


async def is_banned(session: AsyncSession, owner_id: int, other_id: int | None) -> bool:
    """Whether $owner has barred $other from their profile."""
    if other_id is None:
        return False
    return await session.get(ProfileBan, (owner_id, other_id)) is not None


# --- follows ---------------------------------------------------------------------------------------------------------

async def follow(session: AsyncSession, rules: Rules, me: Account, handle: str) -> None:
    """Follows the player. Following someone already followed changes nothing."""
    other = await account_named(session, handle)
    if other.id == me.id:
        raise SocialError("you can't follow yourself")
    if await session.get(Follow, (me.id, other.id)) is not None:
        return
    followed = await session.scalar(select(func.count()).select_from(Follow).where(Follow.follower_id == me.id))
    cap = rules.follow_cap(await playtime.seconds_played(session, rules, me.id))
    if followed >= cap:
        raise SocialError(f"you can follow {cap} players for now: you can follow more the longer you play")
    session.add(Follow(follower_id=me.id, followed_id=other.id))
    await session.flush()


async def unfollow(session: AsyncSession, me: Account, handle: str) -> None:
    other = await account_named(session, handle)
    await session.execute(delete(Follow).where(Follow.follower_id == me.id, Follow.followed_id == other.id))


async def set_share_back(session: AsyncSession, me: Account, handle: str, enabled: bool) -> None:
    """Shows (or stops showing) the followed player your handle and profile link."""
    other = await account_named(session, handle)
    row = await session.get(Follow, (me.id, other.id))
    if row is None:
        raise NotFound("you aren't following that player")
    row.share_back = enabled
    await session.flush()


async def following(session: AsyncSession, rules: Rules, me: Account) -> dict:
    """The caller's list, the players with the most recent activity first (a player who has not played yet last), and the numbers."""
    rows = (await session.execute(
        select(Account.handle, Follow.share_back, PlayerProfile.token, PlayerProfile.directory_link)
        .select_from(Follow).join(Account, Account.id == Follow.followed_id)
        .outerjoin(PlayerProfile, PlayerProfile.account_id == Follow.followed_id)
        .outerjoin(PlayTime, PlayTime.account_id == Follow.followed_id)
        .where(Follow.follower_id == me.id)
        .order_by(func.coalesce(PlayTime.last_input_at, 0).desc(), Account.handle_key)
    )).all()
    cap = rules.follow_cap(await playtime.seconds_played(session, rules, me.id))
    return {
        "following": [
            {"handle": handle, "share_back": share, "profile": token if link else None}
            for handle, share, token, link in rows
        ],
        "cap": cap,
        "followers": await follower_count(session, me),
    }


async def follower_count(session: AsyncSession, me: Account) -> int:
    """How many follow the caller (not counting players the caller banned: their follow is overridden)."""
    banned = select(ProfileBan.banned_id).where(ProfileBan.owner_id == me.id)
    return await session.scalar(select(func.count()).select_from(Follow).where(Follow.followed_id == me.id, Follow.follower_id.not_in(banned))) or 0


async def followers(session: AsyncSession, me: Account) -> dict:
    """How many follow the caller, and those of them who chose to show their handle and profile link."""
    banned = select(ProfileBan.banned_id).where(ProfileBan.owner_id == me.id)
    rows = (await session.execute(
        select(Account.handle, PlayerProfile.token)
        .select_from(Follow).join(Account, Account.id == Follow.follower_id)
        .outerjoin(PlayerProfile, PlayerProfile.account_id == Follow.follower_id)
        .where(Follow.followed_id == me.id, Follow.share_back.is_(True), Follow.follower_id.not_in(banned))
        .order_by(Account.handle_key)
    )).all()
    return {"count": await follower_count(session, me), "sharing": [{"handle": handle, "profile": token} for handle, token in rows]}


# --- bans ------------------------------------------------------------------------------------------------------------

async def ban(session: AsyncSession, me: Account, handle: str) -> None:
    """Bars the player from the caller's profile. Their follow of the caller stays but counts for nothing, and their share-back ends."""
    other = await account_named(session, handle)
    if other.id == me.id:
        raise SocialError("you can't ban yourself")
    if await session.get(ProfileBan, (me.id, other.id)) is None:
        session.add(ProfileBan(owner_id=me.id, banned_id=other.id))
    row = await session.get(Follow, (other.id, me.id))
    if row is not None:
        row.share_back = False
    await session.flush()


async def unban(session: AsyncSession, me: Account, handle: str) -> None:
    other = await account_named(session, handle)
    await session.execute(delete(ProfileBan).where(ProfileBan.owner_id == me.id, ProfileBan.banned_id == other.id))


async def bans(session: AsyncSession, me: Account) -> list[str]:
    """The handles the caller has banned, by handle."""
    return list((await session.scalars(
        select(Account.handle).join(ProfileBan, ProfileBan.banned_id == Account.id).where(ProfileBan.owner_id == me.id).order_by(Account.handle_key)
    )).all())


# --- the directory ---------------------------------------------------------------------------------------------------

async def search(session: AsyncSession, me: Account, prefix: str) -> list[dict]:
    """Players who opted in whose handle starts with $prefix (case and spacing aside), at most ``RESULTS``, by handle."""
    key = handle_key(prefix)
    if not key:
        return []
    barred = select(ProfileBan.owner_id).where(ProfileBan.banned_id == me.id)
    rows = (await session.execute(
        select(Account.id, Account.handle, PlayerProfile.bio, PlayerProfile.token, PlayerProfile.directory_link)
        .join(PlayerProfile, PlayerProfile.account_id == Account.id)
        .where(PlayerProfile.directory.is_(True), Account.handle_key.startswith(key, autoescape=True), Account.id != me.id, Account.id.not_in(barred))
        .order_by(Account.handle_key).limit(RESULTS)
    )).all()
    followed = set((await session.scalars(select(Follow.followed_id).where(Follow.follower_id == me.id, Follow.followed_id.in_([row[0] for row in rows])))).all()) if rows else set()
    return [
        {"handle": handle, "bio": bio, "following": id_ in followed, "profile": token if link else None}
        for id_, handle, bio, token, link in rows
    ]
