"""Housekeeping: small jobs that tidy up what has gone stale, run on a timer (``Settings.housekeeping_seconds``).

A job is an async function ``(session, now, settings, rules)`` registered with ``@job``; ``run`` gives each its own transaction, so
one failing job is logged and does not stop the others. ``now`` is the wall clock's timestamp (``wallclock``). A job deletes
only what nothing reads any more, so running one twice, or concurrently on two servers, is harmless.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import wallclock
from .accounts.ratelimit import ALL_LIMITS
from .alliances.models import Ballot, BallotVote, BallotVoter
from .chat.models import OPEN as FLAG_OPEN
from .chat.models import ChatFlag, ChatMessage
from .voice.discord import VoiceError, provider_for
from .voice.models import VoiceRoom
from .fights.models import PendingDrop
from .fights.pending import OPEN, expire
from .contracts import service as contracts_service
from .contracts.models import ACTIVE, Contract
from .economy import TeamGold
from .fights.rules import Rules
from .fighttags import service as fighttags
from .marketplace import service as marketplace
from .marketplace.hooks import Marketplace
from .models import RateLimitHit, RememberedBrowser, WebauthnKey
from .oidc import service as oidc
from .quests import needs
from .realms import strength
from .relations.models import ANSWERED, DISMISSED, RatingPrompt
from .settings import Settings
from .storage.hooks import NoStorage
from .standing import service as standing

log = logging.getLogger(__name__)

DAY = 24 * 60 * 60
BATCH = 500

Job = Callable[..., Awaitable[int]]

#: Every job, by name, in the order they run. A job returns how many rows it removed.
JOBS: dict[str, Job] = {}


def job(function: Job) -> Job:
    JOBS[function.__name__] = function
    return function


@job
async def finished_rate_limit_windows(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Rate-limit counters whose window is over. A subject's old rows also go when it hits the limit again, but a
    one-off address never does, so they would pile up."""
    finished = or_(*(and_(RateLimitHit.bucket == limit.name, RateLimitHit.window_start <= now - limit.window) for limit in ALL_LIMITS))
    return (await session.execute(delete(RateLimitHit).where(finished))).rowcount


@job
async def stale_pending_drops(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Pending drops that have waited longer than ``Settings.pending_drop_timeout_seconds`` (0: never): a need/want drop is
    settled with the answers so far, one nobody answered (or a hand-out) is left unclaimed. Returns how many it settled."""
    if not settings.pending_drop_timeout_seconds:
        return 0
    cutoff = wallclock.now() - timedelta(seconds=settings.pending_drop_timeout_seconds)
    stale = await session.scalars(select(PendingDrop).where(PendingDrop.status == OPEN, PendingDrop.created_at <= cutoff).order_by(PendingDrop.id).with_for_update(skip_locked=True))
    count = 0
    for pending in stale.all():
        await expire(session, pending)
        count += 1
    return count


@job
async def old_closed_ballots(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Ballots closed longer ago than ``ballot_retention_days``, with their votes (0 days: kept for good)."""
    if not settings.ballot_retention_days:
        return 0
    old = (await session.scalars(select(Ballot.id).where(Ballot.closed_at.is_not(None), Ballot.closed_at <= now - settings.ballot_retention_days * DAY))).all()
    removed = 0
    # Ids first, in batches: MySQL refuses a DELETE whose subquery reads the table it deletes from.
    for start in range(0, len(old), BATCH):
        ids = old[start : start + BATCH]
        await session.execute(delete(BallotVote).where(BallotVote.ballot_id.in_(ids)))
        await session.execute(delete(BallotVoter).where(BallotVoter.ballot_id.in_(ids)))
        removed += (await session.execute(delete(Ballot).where(Ballot.id.in_(ids)))).rowcount
    return removed


@job
async def old_settled_rating_prompts(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Rating prompts answered or dismissed longer ago than ``rating_prompt_retention_days``; a pending one waits for its
    player however old it is (0 days: kept for good)."""
    if not settings.rating_prompt_retention_days:
        return 0
    before = wallclock.now() - timedelta(days=settings.rating_prompt_retention_days)
    settled = RatingPrompt.state.in_([ANSWERED, DISMISSED]) & (RatingPrompt.updated_at <= before)
    return (await session.execute(delete(RatingPrompt).where(settled))).rowcount


@job
async def old_chat_messages(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Chat messages older than ``chat_retention_days`` (0 days: kept for good). A message an admin has yet to settle a flag on is
    kept, and so are the settled flags of a message until it too is old enough; the flags go first so their messages can follow."""
    if not settings.chat_retention_days:
        return 0
    before = wallclock.now() - timedelta(days=settings.chat_retention_days)
    await session.execute(delete(ChatFlag).where(ChatFlag.state != FLAG_OPEN, ChatFlag.updated_at <= before))
    flagged = select(ChatFlag.message_id)
    return (await session.execute(delete(ChatMessage).where(ChatMessage.created_at <= before, ChatMessage.id.not_in(flagged)))).rowcount


@job
async def old_voice_rooms(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Voice rooms made more than ``voice_room_hours`` ago: closed at the provider, then forgotten. A room the provider would not close
    is kept for the next pass (so nothing is left behind there). With voice off, nothing is done."""
    provider = provider_for(settings)
    if provider is None:
        return 0
    before = wallclock.now() - timedelta(hours=settings.voice_room_hours)
    removed = 0
    for room in (await session.scalars(select(VoiceRoom).where(VoiceRoom.created_at <= before).order_by(VoiceRoom.id).limit(BATCH))).all():
        try:
            await provider.close_room(room.provider_id)
        except VoiceError:
            log.warning("a voice room could not be closed; it will be tried again")
            continue
        await session.delete(room)
        removed += 1
    return removed


@job
async def ended_standing_statuses(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Standing statuses whose end time has passed. Every read already leaves them out, so this only tidies the rows."""
    return len(await standing.sweep(session))


@job
async def expired_remembered_browsers(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Remembered browsers whose time is up: they no longer skip anything, so nothing reads them."""
    return (await session.execute(delete(RememberedBrowser).where(RememberedBrowser.expires_at <= wallclock.now()))).rowcount


@job
async def unconfirmed_security_keys(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Security keys whose emailed link was never followed (the link works for an hour; a day is plenty)."""
    cutoff = wallclock.now() - timedelta(days=1)
    return (
        await session.execute(delete(WebauthnKey).where(WebauthnKey.confirmed_at.is_(None), WebauthnKey.created_at <= cutoff))
    ).rowcount


@job
async def expired_oidc_codes_and_tokens(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Sign-in provider codes and access tokens that have run out. Nothing accepts them any more; this only tidies the rows."""
    return await oidc.remove_expired(session, now)


@job
async def old_unkept_fights(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Finished fights that ended more than ``Rules.fight_keep_days`` days ago and that no team or alliance holds (a tag, a top list or a
    slot), with their rounds and what belongs to them (0 days: kept for good)."""
    return await fighttags.sweep(session, rules or Rules(), now)


@job
async def quest_needs(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Looks for gaps in the world's progression and makes a quest for each (``quests.needs``), when the game lists bands (the game's
    ``Needs`` is in ``session.info["needs"]``). Unlike the other jobs it adds rows: it returns how many proposals it made."""
    hooks = session.info.get("needs")
    return len(await needs.run(session, hooks, rules or Rules())) if hooks is not None else 0


@job
async def realm_strength(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Brings every realm's strength up to the world clock and lets settlements fall or be founded as the game's ``Realms`` says
    (``realms.strength.judge``). Unlike the other jobs it changes the world: it returns how many settlements fell or were founded."""
    game = session.info.get("game")
    return await strength.judge(session, game.realms) if game is not None else 0


def _trade(session: AsyncSession):
    """The game's economy, storage, marketplace and contract rules for the trading jobs (``session.info["game"]``), or the engine's own
    when the app runs without a game."""
    game = session.info.get("game")
    if game is None:
        from .contracts.terms import StandardTerms

        return TeamGold(), NoStorage(), Marketplace(), StandardTerms()
    return game.economy, game.storage, game.marketplace, game.contracts


@job
async def ended_listings(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Marketplace listings that ran out of time (``marketplace.expire_due``): the goods go back to their sellers, and an auction with a
    bid is sold to the highest bidder. Unlike the other jobs it moves goods, so it returns how many it settled."""
    economy, storage, hooks, _contracts = _trade(session)
    return await marketplace.expire_due(session, economy, storage, BATCH, hooks)


@job
async def unanswered_contract_offers(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Contract offers nobody answered in time (``contracts.expire_offers``): the offering side's escrow goes back. Returns how many."""
    economy, storage, _hooks, _contracts = _trade(session)
    return await contracts_service.expire_offers(session, economy, storage, BATCH)


@job
async def running_contracts(session: AsyncSession, now: int, settings: Settings, rules: Rules | None = None) -> int:
    """Asks the game about every active contract (``contracts.judge``); one whose terms are all met, or one broken, or one out of time is
    settled and its escrows paid. Returns how many it settled. A contract whose payout can't be made stays active and is tried again."""
    economy, storage, _hooks, contracts = _trade(session)
    settled = 0
    for contract_id in (await session.scalars(select(Contract.id).where(Contract.state == ACTIVE).order_by(Contract.ends_at).limit(BATCH))).all():
        try:
            if (await contracts_service.judge(session, economy, storage, contracts, contract_id)).state != ACTIVE:
                settled += 1
        except contracts_service.ContractError:
            continue
    return settled


async def run(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, rules: Rules | None = None, needs_hooks=None, game=None,
) -> dict[str, int]:
    """One pass: every job in its own transaction. Returns how many rows each removed (a job that failed is left out), and for ``quest_needs``
    how many proposals it made. $needs_hooks is the game's ``Needs``, if it has one, and $game the game, for the trading jobs."""
    now = wallclock.timestamp()
    removed: dict[str, int] = {}
    for name, work in JOBS.items():
        try:
            async with sessionmaker() as session, session.begin():
                session.info["needs"] = needs_hooks
                session.info["game"] = game
                removed[name] = await work(session, now, settings, rules or Rules())
        except Exception:  # one bad job must not stop the others
            log.exception("housekeeping job %s failed", name)
    return removed
