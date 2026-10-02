"""Tokens: a currency the game defines the meaning of. The engine keeps each account's balance and a ledger of every
change, pays what a fight earns (``Rules.tokens_earned``), and lets the game spend through one function (``change``).

An admin earns tokens only when fighting as a player: tokens are paid to the account that owns a *hero* in the fight,
never for a monster, a fight someone watched or a fight someone edited, whoever the account is. No call a player can
make sets ``is_admin``: ``set_admin`` is for a game's own setup code.
"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.fight import Fight
from ..fights.models import FightRecord
from ..fights.rules import Rules
from ..heroes.models import Hero
from ..models import Account
from .models import TokenBalance, TokenEntry


class NotEnoughTokens(ValueError):
    """The balance holds fewer tokens than a spend asked for."""


async def balance(session: AsyncSession, account_id: int) -> int:
    return await session.scalar(select(TokenBalance.balance).where(TokenBalance.account_id == account_id)) or 0


async def audit(session: AsyncSession, account_id: int) -> bool:
    """Whether the balance is what the ledger adds up to."""
    total = await session.scalar(select(func.coalesce(func.sum(TokenEntry.amount), 0)).where(TokenEntry.account_id == account_id))
    return total == await balance(session, account_id)


async def change(session: AsyncSession, account_id: int, amount: int, reason: str, fight_id: int | None = None) -> bool:
    """Adds $amount (negative to spend) to the account's balance and records it. Raises NotEnoughTokens, changing
    nothing, if a spend would take the balance below zero. With a $fight_id, returns False and changes nothing if that
    fight has paid this account already; True otherwise."""
    if amount == 0:
        return False
    if await session.get(TokenBalance, account_id) is None:
        try:
            async with session.begin_nested():
                session.add(TokenBalance(account_id=account_id, balance=0))
        except IntegrityError:  # another call made it first
            pass
    try:
        async with session.begin_nested():
            session.add(TokenEntry(account_id=account_id, amount=amount, reason=reason, fight_id=fight_id))
            await session.flush()
            table = TokenBalance
            moved = await session.execute(
                update(table).where(table.account_id == account_id, table.balance + amount >= 0).values(balance=table.balance + amount)
            )
            if moved.rowcount == 0:
                raise NotEnoughTokens("not enough tokens")
    except IntegrityError:
        return False  # this fight has paid this account
    await session.refresh(await session.get(TokenBalance, account_id), ["balance"])
    return True


async def set_admin(session: AsyncSession, account_id: int, is_admin: bool = True) -> None:
    """Marks an account as an admin (or not). For a game's own setup; there is no call for it."""
    await session.execute(update(Account).where(Account.id == account_id).values(is_admin=is_admin))
    await session.flush()


async def pay_fight(session: AsyncSession, record: FightRecord, fight: Fight, rules: Rules) -> dict[int, int]:
    """Pays the tokens a finished fight earns, once per account ($record remembers through the ledger, so calling it
    again pays nothing more). ``Rules.tokens_earned`` says what each hero's fighter earns; an account gets the sum for
    its heroes. Returns what was paid now, by account id."""
    earned: dict[int, int] = {}
    for address in fight.addresses():
        charid = fight.get(address).charid
        hero = await session.get(Hero, charid) if charid is not None else None
        if hero is None:  # a monster, or a hero deleted since the fight began
            continue
        amount = max(0, int(rules.tokens_earned(fight, address)))
        if amount:
            earned[hero.account_id] = earned.get(hero.account_id, 0) + amount
    paid = {}
    for account_id, amount in sorted(earned.items()):
        if await change(session, account_id, amount, "fight", record.id):
            paid[account_id] = amount
    return paid
