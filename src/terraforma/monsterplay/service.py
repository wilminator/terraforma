"""Monster-party play: the queue and the seating (slice a), and commanding the monsters (slice b).

A player volunteers (``join``) and keeps saying they are still there (``check_in``). When the server creates a fight that
allows it (``FightRecord.monster_play``: random encounters and the hub's Leave Town, never PvP or coliseum fights), it seats
the longest-waiting volunteer the game's rules accept (``seat``) as controller of the monsters. Nobody queued, or nobody
accepted: the AI plays them, as always. A volunteer is seated only when a fight is created, never into a running one, and the
fight never waits for anyone.

The seated player gets the heroes' view and command call for every fighter of the monster party (``fights.live``), and rounds wait for
them like for any player. A round in which they gave none of their fighters a command is *missed*: their monsters repeat their last
commands, the miss is counted, and ``Rules.monster_absent`` may hand the monsters to the AI until the player commands again or says they
are back (``come_back``). Only an admin may sit in a running fight (``take_over``), and the round's end is pushed back for them.

The game decides who may queue (``Rules.may_queue_monsters``) and who may take a seat (``Rules.may_play_monsters``). Both
see a ``Volunteer`` whose ``account_id`` may be None: a guest (slice d), someone who is not logged in, holds only a random token
(``MonsterGuest``) and is told apart by ``guest_id``. The engine's rules refuse guests; a game that wants them says so. A guest earns
no Challenge Tokens and has no lockout, and plays through the guest calls (``monsterplay.routes``), never as an account.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..challenge import service as challenge
from ..fights.fight import Fight
from ..fights.models import FightActionRecord, FightParticipant, FightRecord
from ..fights.rules import Rules
from ..heroes.models import Hero, Team, TeamMember
from ..models import Account
from ..relations import service as relationships
from ..relations.hooks import Ref, Relations
from .facts import Seating, SeatResult, Volunteer
from .models import MonsterGuest, MonsterLockout, MonsterSeat, MonsterVolunteer

#: The party the monsters are in a fight the engine starts (the heroes are party 0).
MONSTER_PARTY = 1


class MonsterError(ValueError):
    """Something the caller can fix: the message says what."""


def _aware(moment: datetime | None) -> datetime | None:
    return moment if moment is None or moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Who:
    """A volunteer: an account, or a guest (exactly one is set)."""

    account_id: int | None = None
    guest_id: int | None = None

    @property
    def holder(self) -> int:
        return self.account_id if self.account_id is not None else -self.guest_id

    def queue_clause(self):
        return MonsterVolunteer.account_id == self.account_id if self.account_id is not None else MonsterVolunteer.guest_id == self.guest_id

    def seat_clause(self):
        return MonsterSeat.account_id == self.account_id if self.account_id is not None else MonsterSeat.guest_id == self.guest_id


async def guest_facts(session: AsyncSession, guest_id: int) -> Volunteer:
    """What the rules are told about a guest: no account, no teams, never an admin, never locked out; only whether they sit in a fight."""
    seated = await session.scalar(
        select(MonsterSeat.fight_id).join(FightRecord, FightRecord.id == MonsterSeat.fight_id)
        .where(MonsterSeat.guest_id == guest_id, FightRecord.finished.is_(False)).limit(1)
    )
    return Volunteer(None, False, (), seated is not None, None, guest_id)


async def facts(session: AsyncSession, account_id: int) -> Volunteer:
    """What the rules are told about an account that wants to command monsters."""
    account = await session.get(Account, account_id)
    teams = (await session.scalars(select(Team.id).where(Team.account_id == account_id).order_by(Team.id))).all()
    as_hero = await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id).join(Hero, Hero.id == FightParticipant.hero_id)
        .where(Hero.account_id == account_id, FightRecord.finished.is_(False), FightParticipant.fled.is_(False)).limit(1)
    )
    as_monsters = await session.scalar(
        select(MonsterSeat.fight_id).join(FightRecord, FightRecord.id == MonsterSeat.fight_id)
        .where(MonsterSeat.account_id == account_id, FightRecord.finished.is_(False)).limit(1)
    )
    lockout = await session.get(MonsterLockout, account_id)
    locked = _aware(lockout.until) if lockout is not None and _aware(lockout.until) > wallclock.now() else None
    return Volunteer(account_id, bool(account and account.is_admin), tuple(teams), as_hero is not None or as_monsters is not None, locked)


def _stale_before(rules: Rules):
    return wallclock.now() - timedelta(seconds=rules.monster_checkin_seconds)


async def _drop_stale(session: AsyncSession, rules: Rules) -> None:
    await session.execute(delete(MonsterVolunteer).where(MonsterVolunteer.checked_in < _stale_before(rules)))


async def _facts_of(session: AsyncSession, who: Who) -> Volunteer:
    return await facts(session, who.account_id) if who.account_id is not None else await guest_facts(session, who.guest_id)


async def _queued(session: AsyncSession, who: Who) -> MonsterVolunteer | None:
    return await session.scalar(select(MonsterVolunteer).where(who.queue_clause()))


async def queued(session: AsyncSession, account_id: int) -> MonsterVolunteer | None:
    return await _queued(session, Who(account_id=account_id))


async def _join(session: AsyncSession, rules: Rules, who: Who) -> MonsterVolunteer:
    now = wallclock.now()
    row = await _queued(session, who)
    if row is not None and _aware(row.checked_in) >= _stale_before(rules):
        row.checked_in = now
        await session.flush()
        return row
    if reason := rules.may_queue_monsters(await _facts_of(session, who)):
        raise MonsterError(reason)
    if row is None:
        row = MonsterVolunteer(account_id=who.account_id, guest_id=who.guest_id, queued_at=now, checked_in=now)
        session.add(row)
    else:  # it had gone away: it waits again from now
        row.queued_at, row.checked_in = now, now
    await session.flush()
    return row


async def join(session: AsyncSession, rules: Rules, account_id: int) -> MonsterVolunteer:
    """Puts the account in the queue (or, if it is there, checks it in: its place is kept unless it had gone stale). Raises
    MonsterError with the rules' reason (``Rules.may_queue_monsters``) when the account may not volunteer."""
    return await _join(session, rules, Who(account_id=account_id))


async def _leave(session: AsyncSession, who: Who) -> bool:
    result = await session.execute(delete(MonsterVolunteer).where(who.queue_clause()))
    return result.rowcount > 0


async def leave(session: AsyncSession, account_id: int) -> bool:
    """Takes the account out of the queue. True if it was in. (Leaving a seat is abandoning a fight, which is another matter.)"""
    return await _leave(session, Who(account_id=account_id))


async def _check_in(session: AsyncSession, rules: Rules, who: Who) -> MonsterVolunteer:
    row = await _queued(session, who)
    if row is None or _aware(row.checked_in) < _stale_before(rules):
        raise MonsterError("you are not in the monster queue")
    row.checked_in = wallclock.now()
    await session.flush()
    return row


async def check_in(session: AsyncSession, rules: Rules, account_id: int) -> MonsterVolunteer:
    """Says the volunteer is still there. Raises MonsterError if they are not in the queue (or had gone stale: they join again)."""
    return await _check_in(session, rules, Who(account_id=account_id))


async def position(session: AsyncSession, row: MonsterVolunteer) -> int:
    """Where the volunteer stands in the queue: 1 is next."""
    ahead = await session.scalar(
        select(func.count()).select_from(MonsterVolunteer).where(
            or_(MonsterVolunteer.queued_at < row.queued_at, (MonsterVolunteer.queued_at == row.queued_at) & (MonsterVolunteer.id < row.id))
        )
    )
    return (ahead or 0) + 1


async def _seat_of(session: AsyncSession, who: Who) -> MonsterSeat | None:
    return await session.scalar(
        select(MonsterSeat).join(FightRecord, FightRecord.id == MonsterSeat.fight_id).where(who.seat_clause(), FightRecord.finished.is_(False)).limit(1)
    )


async def seat_of(session: AsyncSession, account_id: int) -> MonsterSeat | None:
    """The unfinished fight the account commands the monsters of, if any."""
    return await _seat_of(session, Who(account_id=account_id))


async def _status(session: AsyncSession, rules: Rules, who: Who) -> dict:
    row = await _queued(session, who)
    if row is not None and _aware(row.checked_in) < _stale_before(rules):
        row = None
    seat = await _seat_of(session, who)
    volunteer = await _facts_of(session, who)
    return {
        "queued": row is not None,
        "position": await position(session, row) if row is not None else None,
        "seat": None if seat is None else {"fight": seat.fight_id, "party": seat.party},
        "locked_until": volunteer.locked_until.isoformat() if volunteer.locked_until else None,
        "refusal": None if row is not None else rules.may_queue_monsters(volunteer),
        "checkin_seconds": rules.monster_checkin_seconds,
    }


async def status(session: AsyncSession, rules: Rules, account_id: int) -> dict:
    """What the account's page shows: whether it waits and where, the seat it holds, a lockout, and why it may not volunteer."""
    return await _status(session, rules, Who(account_id=account_id))


async def _allied(session: AsyncSession, relations: Relations, teams: tuple[int, ...], hero_teams: list[int]) -> bool:
    """Whether any of $teams counts any of the $hero_teams as an ally (what a relationship that does not exist yet would start at counts too)."""
    for mine in teams:
        for theirs in hero_teams:
            one, other = Ref("team", mine), Ref("team", theirs)
            row = await relationships.get(session, one, other)
            if relations.stance(row.score if row is not None else await relations.initial(session, one, other)) == "ally":
                return True
    return False


async def seat(session: AsyncSession, rules: Rules, relations: Relations, record: FightRecord) -> MonsterSeat | None:
    """Seats the longest-waiting volunteer the game accepts (``Rules.may_play_monsters``) as controller of the new fight's
    monsters, and takes them out of the queue. None if the fight does not allow it, is already seated, or nobody fits; a volunteer
    the rules refuse for this fight stays queued for the next. Volunteers who stopped checking in are dropped first."""
    if not record.monster_play:
        return None
    if (seated := await session.get(MonsterSeat, record.id)) is not None:
        return seated
    await _drop_stale(session, rules)
    hero_ids = (await session.scalars(select(FightParticipant.hero_id).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id.is_not(None)))).all()
    hero_accounts = set((await session.scalars(select(Hero.account_id).where(Hero.id.in_(hero_ids)))).all())
    hero_teams = list((await session.scalars(select(TeamMember.team_id).where(TeamMember.hero_id.in_(hero_ids)).distinct())).all())
    waiting = (await session.scalars(select(MonsterVolunteer).order_by(MonsterVolunteer.queued_at, MonsterVolunteer.id))).all()
    for row in waiting:
        who = Who(row.account_id, row.guest_id)
        volunteer = await _facts_of(session, who)
        seating = Seating(record.id, row.account_id in hero_accounts, await _allied(session, relations, volunteer.team_ids, hero_teams))
        if rules.may_play_monsters(volunteer, seating) is not None:
            continue
        taken = await session.execute(delete(MonsterVolunteer).where(MonsterVolunteer.id == row.id))
        if taken.rowcount == 0:  # another fight seated them first
            continue
        seated = MonsterSeat(fight_id=record.id, party=MONSTER_PARTY, account_id=row.account_id, guest_id=row.guest_id, seated_at=wallclock.now())
        session.add(seated)
        await session.flush()
        return seated
    return None


# --- guests ------------------------------------------------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def new_guest(session: AsyncSession, rules: Rules) -> tuple[MonsterGuest, str]:
    """Makes a guest and returns it with its token, which is shown once (only its hash is kept). Guests that lapsed and sit in no
    fight are cleared away first, with their places in the queue."""
    now = wallclock.now()
    lapsed = select(MonsterGuest.id).where(MonsterGuest.expires_at < now)
    await session.execute(delete(MonsterVolunteer).where(MonsterVolunteer.guest_id.in_(lapsed)))
    seated = select(MonsterSeat.guest_id).where(MonsterSeat.guest_id.is_not(None))
    await session.execute(delete(MonsterGuest).where(MonsterGuest.expires_at < now, MonsterGuest.id.not_in(seated)))
    token = secrets.token_urlsafe(32)
    guest = MonsterGuest(token_hash=_digest(token), created_at=now, expires_at=now + timedelta(seconds=rules.monster_guest_seconds))
    session.add(guest)
    await session.flush()
    return guest, token


async def guest_for(session: AsyncSession, rules: Rules, token: str) -> MonsterGuest | None:
    """The guest a token belongs to, or None if it is unknown or lapsed. Using it keeps it alive for another
    ``Rules.monster_guest_seconds``."""
    guest = await session.scalar(select(MonsterGuest).where(MonsterGuest.token_hash == _digest(token)))
    now = wallclock.now()
    if guest is None or _aware(guest.expires_at) < now:
        return None
    guest.expires_at = now + timedelta(seconds=rules.monster_guest_seconds)
    await session.flush()
    return guest


async def join_guest(session: AsyncSession, rules: Rules, guest_id: int) -> MonsterVolunteer:
    """``join`` for a guest: the rules (``Rules.may_queue_monsters``) see a ``Volunteer`` with no account and this ``guest_id``."""
    return await _join(session, rules, Who(guest_id=guest_id))


async def leave_guest(session: AsyncSession, guest_id: int) -> bool:
    return await _leave(session, Who(guest_id=guest_id))


async def check_in_guest(session: AsyncSession, rules: Rules, guest_id: int) -> MonsterVolunteer:
    return await _check_in(session, rules, Who(guest_id=guest_id))


async def status_guest(session: AsyncSession, rules: Rules, guest_id: int) -> dict:
    return await _status(session, rules, Who(guest_id=guest_id))


async def seat_of_guest(session: AsyncSession, guest_id: int) -> MonsterSeat | None:
    return await _seat_of(session, Who(guest_id=guest_id))


# --- commanding the monsters ----------------------------------------------------------------------------------------

async def last_commands(session: AsyncSession, record: FightRecord, played: int) -> dict[tuple, dict]:
    """The commands of the last round that was played ($played rounds so far), as they were stored, by fighter address."""
    row = await session.get(FightActionRecord, (record.id, played)) if played else None
    return {tuple(each["address"]): each for each in row.commands} if row is not None else {}


def view_of(seat: MonsterSeat) -> dict:
    return {"party": seat.party, "absent": seat.absent, "admin": seat.admin}


async def come_back(session: AsyncSession, record: FightRecord, holder: int) -> MonsterSeat:
    """The player ($holder: an account's id, or a guest's made negative, see ``MonsterSeat.holder``) says they are back, so they
    command their monsters again from the next round. Raises MonsterError if they do not hold the fight's seat."""
    seat = await session.get(MonsterSeat, record.id)
    if seat is None or seat.holder != holder:
        raise MonsterError("you are not commanding the monsters of that fight")
    seat.absent = False
    await session.flush()
    return seat


async def take_over(session: AsyncSession, rules: Rules, record: FightRecord, account_id: int) -> MonsterSeat:
    """An admin sits in a running fight as the controller of its monsters, and the round's end is pushed back by a whole round (never
    earlier than it was) so they can take in what is happening and decide. They take an empty seat or one whose player is absent (the
    AI is playing), never one a player is commanding. A fight must allow monster play (``FightRecord.monster_play``), and an admin with a
    hero in it may not. Taking over earns nothing (``MonsterSeat.admin``). Raises MonsterError."""
    account = await session.get(Account, account_id)
    if account is None or not account.is_admin:
        raise MonsterError("only an admin may sit in a running fight")
    if record.finished:
        raise MonsterError("that fight is over")
    if not record.monster_play:
        raise MonsterError("that fight has no monsters a player can command")
    if (await _hero_accounts(session, record)) & {account_id}:
        raise MonsterError("you have a hero in that fight")
    seat = await session.get(MonsterSeat, record.id)
    if seat is not None and seat.holder != account_id and not seat.absent:
        raise MonsterError("a player is commanding the monsters")
    if seat is None:
        seat = MonsterSeat(fight_id=record.id, party=MONSTER_PARTY, account_id=account_id, seated_at=wallclock.now(), admin=True)
        session.add(seat)
    else:
        seat.account_id, seat.guest_id, seat.admin, seat.absent = account_id, None, True, False
    pushed = wallclock.now() + timedelta(seconds=rules.round_length(record.time_multiplier))
    if record.round_deadline is not None and _aware(record.round_deadline) < pushed:
        record.round_deadline = pushed
    await session.flush()
    return seat


async def _hero_accounts(session: AsyncSession, record: FightRecord) -> set[int]:
    hero_ids = (await session.scalars(select(FightParticipant.hero_id).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id.is_not(None)))).all()
    return set((await session.scalars(select(Hero.account_id).where(Hero.id.in_(hero_ids)))).all())


# --- when the fight ends ----------------------------------------------------------------------------------------------

async def settle(session: AsyncSession, rules: Rules, record: FightRecord, fight: Fight) -> int:
    """Settles the fight's seat once it has ended: the Challenge Tokens ``Rules.monster_challenge_earned`` says it earned (cut to
    the daily and purse caps; none for a seat an admin took) and, for an account that is not an admin, the lockout
    ``Rules.monster_lockout_seconds`` sets (never shorter than one already running). Safe to call again: a seat settles once. Returns
    the tokens paid."""
    seat = await session.get(MonsterSeat, record.id)
    if seat is None or seat.settled:
        return 0
    claimed = await session.execute(update(MonsterSeat).where(MonsterSeat.fight_id == record.id, MonsterSeat.settled.is_(False)).values(settled=True))
    if claimed.rowcount == 0:  # settled meanwhile
        return 0
    if seat.account_id is None:  # a guest earns nothing and has no lockout
        await session.flush()
        return 0
    party = fight.parties.get(seat.party)
    result = SeatResult(record.id, seat.account_id, seat.party, seat.missed == 0, party is not None and not party.dead(rules), seat.absent, seat.missed, seat.admin)
    paid = 0
    if not seat.admin:
        if (amount := max(0, int(rules.monster_challenge_earned(result)))) > 0:
            paid = await challenge.earn(session, rules, seat.account_id, amount, record.id)
        account = await session.get(Account, seat.account_id)
        if account is not None and not account.is_admin and (seconds := int(rules.monster_lockout_seconds(result))) > 0:
            until = wallclock.now() + timedelta(seconds=seconds)
            lockout = await session.get(MonsterLockout, seat.account_id)
            if lockout is None:
                session.add(MonsterLockout(account_id=seat.account_id, until=until))
            elif _aware(lockout.until) < until:
                lockout.until = until
    await session.flush()
    return paid
