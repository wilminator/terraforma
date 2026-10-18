"""What each fight page shows.

A signed-in player can view a fight (running or finished) that is one of: their own (a hero of theirs is in it); one with a
hero of a player they follow, unless that player has barred them from their profile; one the game marked public
(``Rules.public_fight``); or one in the retained or top fights of an alliance one of their teams is in. Anyone can watch a public
fight, and the best fight of a team whose player made the team visible. The pages show a fight by its public name (``guid``)
and fighters' names, never an account.
"""

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..alliances.models import AllianceMember
from ..fights import live, store
from ..fights.events import Event, EventType
from ..fights.replay import apply_events
from ..fights.state import hydrate
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..fighttags import scoring
from ..fighttags.models import AllianceKept, AllianceTop, FightTeam, TeamKept, TeamTag
from ..heroes.models import Hero, Team
from ..models import World
from ..profiles.models import TeamProfile
from ..social.models import Follow, ProfileBan
from ..world.rng import WorldRng
from .models import PublicFight

REASONS = ("mine", "following", "alliance", "public")


class PageError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(PageError):
    pass


def _at(record: FightRecord) -> str:
    return live.aware(record.updated_at).isoformat()


# --- a team's history -----------------------------------------------------------------------------------------------------

async def team_history(session: AsyncSession, team_id: int, before: int | None = None, limit: int = 20) -> list[dict]:
    """A team's finished fights, newest first (the ones before the fight number $before): how each went, whether the team keeps it,
    and which of its tags it holds."""
    query = select(FightTeam, FightRecord).join(FightRecord, FightRecord.id == FightTeam.fight_id).where(FightTeam.team_id == team_id)
    if before is not None:
        query = query.where(FightTeam.fight_id < before)
    rows = (await session.execute(query.order_by(FightTeam.fight_id.desc()).limit(limit))).all()
    ids = [row.fight_id for row, _record in rows]
    kept = set((await session.scalars(select(TeamKept.fight_id).where(TeamKept.team_id == team_id, TeamKept.fight_id.in_(ids)))).all()) if ids else set()
    tags: dict[int, list[str]] = {}
    for tag in (await session.scalars(select(TeamTag).where(TeamTag.team_id == team_id, TeamTag.fight_id.in_(ids)))).all() if ids else []:
        tags.setdefault(tag.fight_id, []).append(tag.kind)
    return [
        {"id": row.fight_id, "guid": record.guid, "at": _at(record), "pvp": row.pvp, "won": row.won, "overkill": row.overkill,
         "underdog": row.underdog, "kept": row.fight_id in kept, "tags": sorted(tags.get(row.fight_id, []))}
        for row, record in rows
    ]


# --- the fights a player can view ---------------------------------------------------------------------------------------------

def _sources(me_id: int) -> dict[str, Select]:
    """For each reason a player may view a fight, the ids of the fights it covers."""
    mine = select(FightParticipant.fight_id).join(Hero, Hero.id == FightParticipant.hero_id).where(Hero.account_id == me_id)
    followed = (
        select(FightParticipant.fight_id).join(Hero, Hero.id == FightParticipant.hero_id)
        .join(Follow, (Follow.followed_id == Hero.account_id) & (Follow.follower_id == me_id))
        .where(~exists().where(ProfileBan.owner_id == Hero.account_id, ProfileBan.banned_id == me_id))
    )
    my_alliances = select(AllianceMember.alliance_id).join(Team, Team.id == AllianceMember.team_id).where(Team.account_id == me_id)
    alliance = select(AllianceTop.fight_id).where(AllianceTop.alliance_id.in_(my_alliances)).union(
        select(AllianceKept.fight_id).where(AllianceKept.alliance_id.in_(my_alliances))
    )
    return {"mine": mine, "following": followed, "alliance": alliance, "public": select(PublicFight.fight_id)}


async def viewable(session: AsyncSession, me_id: int, before: int | None = None, limit: int = 20) -> list[dict]:
    """The fights the player can view, newest first (the ones before the fight number $before), each with why."""
    why: dict[int, set[str]] = {}
    for reason, ids in _sources(me_id).items():
        query = select(FightRecord.id).where(FightRecord.id.in_(ids))
        if before is not None:
            query = query.where(FightRecord.id < before)
        for fight_id in (await session.scalars(query.order_by(FightRecord.id.desc()).limit(limit))).all():
            why.setdefault(fight_id, set()).add(reason)
    # The newest $limit of all the reasons together are among the newest $limit of one of them.
    chosen = sorted(why, reverse=True)[:limit]
    records = {record.id: record for record in (await session.scalars(select(FightRecord).where(FightRecord.id.in_(chosen)))).all()} if chosen else {}
    return [
        {"id": fight_id, "guid": records[fight_id].guid, "over": records[fight_id].finished, "at": _at(records[fight_id]),
         "why": [reason for reason in REASONS if reason in why[fight_id]]}
        for fight_id in chosen
    ]


async def can_view(session: AsyncSession, me_id: int, fight_id: int) -> bool:
    for ids in _sources(me_id).values():
        if await session.scalar(select(FightRecord.id).where(FightRecord.id == fight_id, FightRecord.id.in_(ids))) is not None:
            return True
    return False


async def replay(session: AsyncSession, rules: Rules, record: FightRecord) -> dict:
    """The fight as it stands and every round played, with the events, so a page can play it back. $start is the fight as a viewer first
    sees it, before round 1, and $joins has, for each round that opens with a party joining, the fight just after that party arrived
    (the events only say who joined, so a page that plays them back picks up from this)."""
    logged = await store.actions(session, record)
    fight = hydrate(record.initial_state)
    start = await live.describe(session, record, rules, fight, 1, False, {}, set(), None)
    joins = {}
    for action in logged:
        events = [Event.from_list(each) for each in action.events]
        arrivals = [each for each in events if each.type is EventType.PARTY_JOINED]
        if arrivals:  # (they come first in the round)
            apply_events(fight, rules, arrivals)
            joins[str(action.sequence)] = await live.describe(session, record, rules, fight, action.sequence, False, {}, set(), None)
        apply_events(fight, rules, [each for each in events if each.type is not EventType.PARTY_JOINED])
    return {
        "fight": await live.view(session, record, rules),
        "start": start,
        "joins": joins,
        "rounds": [{"round": action.sequence, "events": action.events} for action in logged],
    }


async def viewable_replay(session: AsyncSession, rules: Rules, me_id: int, fight_id: int) -> dict:
    record = await session.get(FightRecord, fight_id)
    if record is None or not await can_view(session, me_id, fight_id):
        raise NotFound("there's no such fight")
    return await replay(session, rules, record)


# --- the public pages ---------------------------------------------------------------------------------------------------------

def _showcased() -> Select:
    """The tags of teams whose player made them visible: their best fights may be shown to anyone."""
    return select(TeamTag).join(TeamProfile, TeamProfile.team_id == TeamTag.team_id).where(TeamProfile.visible.is_(True))


async def showcase(session: AsyncSession, rules: Rules, kind: str | None = None) -> dict:
    """A random best fight (an underdog or overkill tag) of a visible team, chosen afresh each second from the world's streams."""
    query = _showcased().where(TeamTag.kind.in_([kind] if kind else list(scoring.KINDS)))
    tags = (await session.scalars(query.order_by(TeamTag.team_id, TeamTag.kind))).all()
    if not tags:
        raise NotFound("there's no fight to show yet")
    world = await session.scalar(select(World).order_by(World.id).limit(1))
    pick = WorldRng(world.seed if world else 0).stream("showcase", wallclock.timestamp()).choice(tags)
    record = await session.get(FightRecord, pick.fight_id)
    return {"kind": pick.kind, "score": pick.score, "guid": record.guid, "replay": await replay(session, rules, record)}


async def coliseum(session: AsyncSession, before: int | None = None, limit: int = 20) -> list[dict]:
    """Every public fight, newest first (the ones before the fight number $before)."""
    query = select(FightRecord).join(PublicFight, PublicFight.fight_id == FightRecord.id)
    if before is not None:
        query = query.where(FightRecord.id < before)
    return [{"id": record.id, "guid": record.guid, "at": _at(record)} for record in (await session.scalars(query.order_by(FightRecord.id.desc()).limit(limit))).all()]


async def public_replay(session: AsyncSession, rules: Rules, guid: str) -> dict:
    """A finished fight anyone may watch (a public one, or the best fight of a visible team) by its public name."""
    record = await session.scalar(select(FightRecord).where(FightRecord.guid == guid, FightRecord.finished.is_(True)))
    if record is None:
        raise NotFound("there's no such fight")
    public = await session.get(PublicFight, record.id) is not None
    shown = await session.scalar(_showcased().where(TeamTag.fight_id == record.id).limit(1)) is not None
    if not (public or shown):
        raise NotFound("there's no such fight")
    return await replay(session, rules, record)
