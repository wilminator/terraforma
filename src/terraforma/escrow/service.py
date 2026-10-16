"""Holding gold, Challenge Tokens and items in escrow, and settling them.

A hero puts a deposit down with a guild (``hold``): the gold leaves the hero's purse (``Economy.debit``), the tokens leave the
account's balance and the chosen stacks leave the hero's pack, all or nothing. The deposit stays with the guild until the game
settles it, once: ``refund`` gives all of it back to the depositing team, ``forfeit`` gives it to another team. Neither is a call a
player can make: what a deposit secures (a contract, a listing, a delivery) and when it is released is the game's, so these are
functions for the game's dialogs and rules.

Paying out is all or nothing too. Gold goes to the team (``Economy.credit_team``), tokens to the team's account and items through the
game's storage (``storage.service.deliver``, into a hero of the receiving team); a team with no room for every item gets nothing, and
the deposit stays held. Tokens are paid back whatever the purse cap says: they were the account's own.
"""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..challenge import service as challenge
from ..content.models import Item
from ..economy import Economy, NotEnoughGold
from ..heroes import inventory
from ..heroes.models import Hero, Team, TeamMember
from ..storage import service as storage_service
from ..storage.hooks import Storage
from .models import FORFEITED, HELD, REFUNDED, Escrow, EscrowItem

ESCROW = "escrow"  # the reason in the Challenge Token ledger
MAX_AMOUNT = 10**9


class EscrowError(ValueError):
    """Something the caller can fix: the message says what."""


async def _team_of(session: AsyncSession, hero: Hero) -> int:
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        raise EscrowError("only a hero on a team can put down a deposit")
    return team_id


async def hold(
    session: AsyncSession, economy: Economy, hero: Hero, guild: str, purpose: str, ref: str = "",
    gold: int = 0, tokens: int = 0, items: Sequence[tuple[int, int]] = (),
) -> Escrow:
    """Takes a deposit from $hero for their team: $gold from the hero's purse, $tokens from their account, and $items, a list of
    (pack position, quantity) pairs, from their pack. Nothing worn can be held. Raises EscrowError, changing nothing, if the
    deposit is empty, anything is short or any stack is missing."""
    if not guild or not purpose:
        raise EscrowError("a deposit needs a guild and a purpose")
    if not (0 <= gold <= MAX_AMOUNT and 0 <= tokens <= MAX_AMOUNT):
        raise EscrowError("gold and tokens are zero or more")
    if not (gold or tokens or items):
        raise EscrowError("there is nothing to hold")
    positions = [position for position, _qty in items]
    if len(set(positions)) != len(positions):
        raise EscrowError("each stack can be named once")
    team_id = await _team_of(session, hero)
    async with session.begin_nested():  # a refusal anywhere gives back what was taken before it
        await session.execute(select(Hero.id).where(Hero.id == hero.id).with_for_update())  # two deposits at once cannot take one stack
        escrow = Escrow(guild=guild, purpose=purpose, ref=ref, team_id=team_id, gold=gold, tokens=tokens)
        session.add(escrow)
        await session.flush()
        if gold:
            try:
                await economy.debit(session, hero, gold)
            except NotEnoughGold as error:
                raise EscrowError("not enough gold") from error
        if tokens:
            try:
                await challenge.change(session, hero.account_id, -tokens, ESCROW)
            except challenge.NotEnoughTokens as error:
                raise EscrowError("not enough tokens") from error
        worn = set((await inventory.equipment(session, hero)).values())
        stacks = {stack.position: (stack, item) for stack, item in await inventory.stacks(session, hero)}
        for position, qty in sorted(items, reverse=True):  # from the end, so taking a stack does not move the next
            found = stacks.get(position)
            if found is None:
                raise EscrowError("there's nothing in that position")
            stack, item = found
            if not 1 <= qty <= stack.qty:
                raise EscrowError(f"you have {stack.qty} of that, and a deposit is at least 1")
            if stack.id in worn:
                raise EscrowError("take it off first")
            if await inventory.remove_item(session, hero, position, qty) != qty:
                raise EscrowError("that is no longer there")
            session.add(EscrowItem(escrow_id=escrow.id, item_id=item.id, qty=qty))
        await session.flush()
    return escrow


async def contents(session: AsyncSession, escrow: Escrow) -> dict:
    """What a deposit holds: its gold, its tokens and its items, as (item key, quantity) pairs."""
    rows = await session.execute(
        select(Item.key, EscrowItem.qty).join(Item, Item.id == EscrowItem.item_id).where(EscrowItem.escrow_id == escrow.id).order_by(EscrowItem.id)
    )
    return {"gold": escrow.gold, "tokens": escrow.tokens, "items": [(key, qty) for key, qty in rows.all()]}


async def held(
    session: AsyncSession, team_id: int | None = None, guild: str | None = None, purpose: str | None = None, ref: str | None = None,
) -> list[Escrow]:
    """The deposits still held, oldest first, narrowed by whichever of team, guild, purpose and ref are given."""
    query = select(Escrow).where(Escrow.state == HELD).order_by(Escrow.id)
    for column, value in ((Escrow.team_id, team_id), (Escrow.guild, guild), (Escrow.purpose, purpose), (Escrow.ref, ref)):
        if value is not None:
            query = query.where(column == value)
    return list((await session.scalars(query)).all())


async def _receiver(session: AsyncSession, team_id: int) -> tuple[Team, Hero]:
    team = await session.get(Team, team_id)
    hero = None if team is None else await session.scalar(
        select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot).limit(1)
    )
    if team is None or hero is None:
        raise EscrowError("the team that would receive it has no hero")
    return team, hero


async def _settle(session: AsyncSession, economy: Economy, storage: Storage, escrow_id: int, state: str, to_team_id: int | None) -> Escrow:
    escrow = await session.get(Escrow, escrow_id)
    if escrow is None:
        raise EscrowError("there is no such deposit")
    receiver_id = escrow.team_id if to_team_id is None else to_team_id
    async with session.begin_nested():  # all of it is paid out, or none of it and the deposit stays held
        team, hero = await _receiver(session, receiver_id)  # before the claim: a team that does not exist breaks the foreign key
        claimed = await session.execute(
            update(Escrow).where(Escrow.id == escrow_id, Escrow.state == HELD).values(state=state, settled_to=receiver_id, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            raise EscrowError("that deposit is settled already")
        if escrow.gold:
            await economy.credit_team(session, team.id, escrow.gold)
        if escrow.tokens:
            await challenge.change(session, team.account_id, escrow.tokens, ESCROW)
        for key, qty in (await contents(session, escrow))["items"]:
            if (await storage_service.deliver(session, storage, hero, key, qty)).left:
                raise EscrowError("the team that would receive it has no room for every item")
    await session.refresh(escrow)
    return escrow


async def refund(session: AsyncSession, economy: Economy, storage: Storage, escrow_id: int) -> Escrow:
    """Gives the whole deposit back to the team that put it down. Raises EscrowError, changing nothing, if it is settled already
    or the team has no hero or no room for every item."""
    return await _settle(session, economy, storage, escrow_id, REFUNDED, None)


async def forfeit(session: AsyncSession, economy: Economy, storage: Storage, escrow_id: int, to_team_id: int) -> Escrow:
    """Gives the whole deposit to $to_team_id (a breach's victim). Raises EscrowError, changing nothing, if it is settled already
    or that team has no hero or no room for every item."""
    return await _settle(session, economy, storage, escrow_id, FORFEITED, to_team_id)
