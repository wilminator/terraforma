"""Challenge Tokens: a special currency, one balance per account, that the game decides the meaning and the limits of.

The engine keeps each account's balance and a ledger of every change, pays what a fight earns (``Rules.challenge_earned``),
lets the game spend through one function (``change``) and, if the game turns it on, lets the game's own backend credit
tokens a player bought (``purchase``, behind the server-to-server call in ``challenge.routes``).

Two limits are the game's to set (``Rules.challenge_daily_cap`` and ``Rules.challenge_purse_cap``). The daily cap limits
what an account *earns from fights* in a UTC day: what would go over it is simply not paid. The purse cap limits what an
account may hold: earnings are cut to fit, and a purchase that would not fit is refused whole (nobody pays for tokens that
are thrown away). Neither limits a spend, and the daily cap does not limit purchases.

An admin earns tokens only when fighting as a player: tokens are paid to the account that owns a *hero* in the fight,
never for a monster, a fight someone watched or a fight someone edited, whoever the account is. No call a player can
make sets ``is_admin``: ``set_admin`` is for a game's own setup code.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..fights.fight import Fight
from ..fights.models import FightRecord
from ..fights.rules import Rules
from ..heroes.models import Hero
from ..models import Account
from .models import ChallengeBalance, ChallengeEntry

FIGHT = "fight"
PURCHASE = "purchase"


class ChallengeError(ValueError):
    """Base of what the Challenge Token functions refuse."""


class NotEnoughTokens(ChallengeError):
    """The balance holds fewer tokens than a spend asked for."""


class PurseFull(ChallengeError):
    """A credit would take the balance over the game's purse cap."""


class UnknownAccount(ChallengeError):
    """No such account."""


class KeyReused(ChallengeError):
    """The idempotency key was used before for a different purchase (another account or amount)."""


@dataclass(frozen=True)
class Credited:
    """What a purchase did: the tokens credited, the balance after it, and whether this was a repeat of a purchase already
    credited (then nothing was credited again, and ``balance`` is what it is now)."""

    amount: int
    balance: int
    duplicate: bool


async def balance(session: AsyncSession, account_id: int) -> int:
    return await session.scalar(select(ChallengeBalance.balance).where(ChallengeBalance.account_id == account_id)) or 0


async def audit(session: AsyncSession, account_id: int) -> bool:
    """Whether the balance is what the ledger adds up to."""
    total = await session.scalar(
        select(func.coalesce(func.sum(ChallengeEntry.amount), 0)).where(ChallengeEntry.account_id == account_id)
    )
    return total == await balance(session, account_id)


def start_of_day():
    now = wallclock.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def earned_today(session: AsyncSession, account_id: int) -> int:
    """What the account has earned from fights since midnight UTC (the wall clock's day, not the world's)."""
    return await session.scalar(
        select(func.coalesce(func.sum(ChallengeEntry.amount), 0)).where(
            ChallengeEntry.account_id == account_id,
            ChallengeEntry.reason == FIGHT,
            ChallengeEntry.created_at >= start_of_day(),
            ChallengeEntry.created_at < start_of_day() + timedelta(days=1),
        )
    )


async def change(
    session: AsyncSession,
    account_id: int,
    amount: int,
    reason: str,
    fight_id: int | None = None,
    key: str | None = None,
    purse_cap: int | None = None,
) -> bool:
    """Adds $amount (negative to spend) to the account's balance and records it, with no daily limit. Raises
    NotEnoughTokens, changing nothing, if a spend would take the balance below zero, and PurseFull, changing nothing, if a
    credit would take it over $purse_cap. With a $fight_id (or a $key), returns False and changes nothing if that fight has
    paid this account (or that key was used) already; True otherwise. A zero amount is recorded only for a fight, to mark
    it paid (a fight whose pay was cut to nothing by the daily cap must not pay again tomorrow)."""
    if amount == 0 and fight_id is None:
        return False
    if await session.get(ChallengeBalance, account_id) is None:
        try:
            async with session.begin_nested():
                session.add(ChallengeBalance(account_id=account_id, balance=0))
        except IntegrityError:  # another call made it first
            pass
    try:
        async with session.begin_nested():
            session.add(
                ChallengeEntry(
                    account_id=account_id, amount=amount, reason=reason, fight_id=fight_id, idempotency_key=key, created_at=wallclock.now()
                )
            )
            await session.flush()
            table = ChallengeBalance
            fits = [table.account_id == account_id, table.balance + amount >= 0]
            if amount > 0 and purse_cap is not None:
                fits.append(table.balance + amount <= purse_cap)
            moved = await session.execute(update(table).where(*fits).values(balance=table.balance + amount))
            if moved.rowcount == 0:
                raise NotEnoughTokens("not enough tokens") if amount < 0 else PurseFull("the purse is full")
    except IntegrityError:
        return False  # this fight has paid this account, or this key was used
    await session.refresh(await session.get(ChallengeBalance, account_id), ["balance"])
    return True


async def earn(session: AsyncSession, rules: Rules, account_id: int, amount: int, fight_id: int) -> int:
    """Pays what an account earned in a fight, cut to fit the game's daily cap and purse cap. Returns what was paid
    (0 if the fight had paid this account already or nothing fits)."""
    daily, purse = rules.challenge_daily_cap(account_id), rules.challenge_purse_cap(account_id)
    if daily is not None:
        amount = min(amount, max(0, daily - await earned_today(session, account_id)))
    if purse is not None:
        amount = min(amount, max(0, purse - await balance(session, account_id)))
    amount = max(0, amount)
    try:
        paid = await change(session, account_id, amount, FIGHT, fight_id, purse_cap=purse)
    except PurseFull:  # another credit took the room since we looked
        paid = await change(session, account_id, 0, FIGHT, fight_id)
        amount = 0
    return amount if paid else 0


async def purchase(session: AsyncSession, rules: Rules, account_id: int, amount: int, key: str) -> Credited:
    """Credits $amount tokens the account bought. $key is the buyer's idempotency key: the same key with the same account and
    amount credits once however often it is sent (the repeats answer as ``duplicate``); the same key for another account or
    amount raises KeyReused. Raises UnknownAccount, and PurseFull (crediting nothing) if it would not fit the purse cap. The
    daily cap does not apply: it limits what is earned, not what is bought."""
    if amount < 1:
        raise ChallengeError("a purchase credits at least one token")
    if await session.get(Account, account_id) is None:
        raise UnknownAccount("no such account")
    seen = await _entry_with_key(session, key)
    if seen is None:
        cap = rules.challenge_purse_cap(account_id)
        if cap is not None and await balance(session, account_id) + amount > cap:
            raise PurseFull("the purse is full")
        if await change(session, account_id, amount, PURCHASE, key=key, purse_cap=cap):
            return Credited(amount, await balance(session, account_id), duplicate=False)
        seen = await _entry_with_key(session, key)  # a call with the same key got in first
    if seen is None or (seen.account_id, seen.amount, seen.reason) != (account_id, amount, PURCHASE):
        raise KeyReused("that key was used for a different purchase")
    return Credited(amount, await balance(session, account_id), duplicate=True)


async def _entry_with_key(session: AsyncSession, key: str) -> ChallengeEntry | None:
    return await session.scalar(select(ChallengeEntry).where(ChallengeEntry.idempotency_key == key))


async def set_admin(session: AsyncSession, account_id: int, is_admin: bool = True) -> None:
    """Marks an account as an admin (or not). For a game's own setup; there is no call for it."""
    await session.execute(update(Account).where(Account.id == account_id).values(is_admin=is_admin))
    await session.flush()


async def pay_fight(session: AsyncSession, record: FightRecord, fight: Fight, rules: Rules) -> dict[int, int]:
    """Pays the tokens a finished fight earns, once per account ($record remembers through the ledger, so calling it
    again pays nothing more). ``Rules.challenge_earned`` says what each hero's fighter earns; an account gets the sum for
    its heroes, cut to fit the game's daily and purse caps. Returns what was paid now, by account id."""
    earned: dict[int, int] = {}
    for address in fight.addresses():
        charid = fight.get(address).charid
        hero = await session.get(Hero, charid) if charid is not None else None
        if hero is None:  # a monster, or a hero deleted since the fight began
            continue
        amount = max(0, int(rules.challenge_earned(fight, address)))
        if amount:
            earned[hero.account_id] = earned.get(hero.account_id, 0) + amount
    paid = {}
    for account_id, amount in sorted(earned.items()):
        if given := await earn(session, rules, account_id, amount, record.id):
            paid[account_id] = given
    return paid
