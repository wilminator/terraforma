"""Giving gold and items to another hero, if the game's economy allows it.

Every refusal reads the same to the giver whether the other hero does not exist or may not be traded with
(``Refused``), so the calls cannot be used to find out which heroes exist. Gold moves through the economy's
purses (``Economy.debit`` and ``credit``); an item moves between inventories, never while it is worn, and only
if it all fits in the other pack. Each gift is one line in the ledger (``TradeRecord``).

Service functions: they raise ``TradeError`` (a message the player can read) and change nothing when they do,
as long as the caller's transaction rolls back (the calls' session does).
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import Economy, NotEnoughGold
from ..heroes import inventory
from ..heroes.models import Hero
from .models import TradeRecord
from .policy import Trade

MAX_GOLD = 10**9


class TradeError(ValueError):
    """Something the player can fix: the message says what."""


class Refused(TradeError):
    """There is no such hero to trade with, or the game does not let these two trade."""


REFUSED = "you can't trade with that hero"


async def _receiver(session: AsyncSession, economy: Economy, giver: Hero, receiver_id: int, what: Trade) -> Hero:
    receiver = await session.get(Hero, receiver_id)
    if receiver is None or receiver.id == giver.id or not await economy.can_trade(session, giver, receiver, what):
        raise Refused(REFUSED)
    return receiver


def _record(session: AsyncSession, giver: Hero, receiver: Hero, what: Trade) -> None:
    session.add(TradeRecord(
        giver_id=giver.id, receiver_id=receiver.id, giver_name=giver.name, receiver_name=receiver.name,
        kind=what.kind, item_key=what.item, qty=what.amount if what.kind == "gold" else what.qty,
    ))


async def give_gold(session: AsyncSession, economy: Economy, giver: Hero, receiver_id: int, amount: int) -> int:
    """Gives $amount gold. Returns what the giver has left."""
    if not 1 <= amount <= MAX_GOLD:
        raise TradeError("give at least 1 gold")
    what = Trade("gold", amount=amount)
    receiver = await _receiver(session, economy, giver, receiver_id, what)
    mine, theirs = await economy.purse(session, giver), await economy.purse(session, receiver)
    if type(mine) is type(theirs) and mine.id == theirs.id:
        raise TradeError("you share one purse: there is nothing to give")
    try:
        await economy.debit(session, giver, amount)
    except NotEnoughGold as error:
        raise TradeError("not enough gold") from error
    await economy.credit(session, receiver, amount)
    _record(session, giver, receiver, what)
    await session.flush()
    return await economy.balance(session, giver)


async def _lock(session: AsyncSession, *hero_ids: int) -> None:
    """Locks the heroes' rows (in id order, so two gifts the other way round cannot wait on each other), so two calls at the
    same instant cannot both give the same stack."""
    for hero_id in sorted(set(hero_ids)):
        await session.execute(select(Hero.id).where(Hero.id == hero_id).with_for_update())


async def give_item(session: AsyncSession, economy: Economy, giver: Hero, receiver_id: int, position: int, qty: int) -> None:
    """Gives $qty of the stack at $position. It must not be worn, and all of it must fit in the other pack."""
    receiver_row = await session.get(Hero, receiver_id)
    await _lock(session, giver.id, receiver_row.id if receiver_row else giver.id)
    found = next(((stack, item) for stack, item in await inventory.stacks(session, giver) if stack.position == position), None)
    if found is None:
        raise TradeError("there's nothing in that position")
    stack, item = found
    if not 1 <= qty <= stack.qty:
        raise TradeError(f"you have {stack.qty} of that, and a gift is at least 1")
    if stack.id in (await inventory.equipment(session, giver)).values():
        raise TradeError("take it off first")
    what = Trade("item", item=item.key, qty=qty)
    receiver = await _receiver(session, economy, giver, receiver_id, what)
    async with session.begin_nested():  # a gift that does not all fit leaves the other pack as it was
        if await inventory.add_item(session, receiver, item.key, qty):
            raise TradeError("their pack has no room for that")
    if await inventory.remove_item(session, giver, position, qty) != qty:
        raise TradeError("that is no longer there")
    _record(session, giver, receiver, what)
    await session.flush()


async def history(session: AsyncSession, hero: Hero, limit: int = 50) -> list[dict]:
    """What the hero gave and received, newest first."""
    rows = await session.scalars(
        select(TradeRecord).where(or_(TradeRecord.giver_id == hero.id, TradeRecord.receiver_id == hero.id))
        .order_by(TradeRecord.id.desc()).limit(limit)
    )
    return [
        {
            "id": row.id, "direction": "gave" if row.giver_id == hero.id else "received",
            "other": row.receiver_name if row.giver_id == hero.id else row.giver_name,
            "kind": row.kind, "item": row.item_key or None, "qty": row.qty, "at": row.created_at.isoformat(),
        }
        for row in rows.all()
    ]
