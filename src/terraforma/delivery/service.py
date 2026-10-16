"""Sending goods to a team's guild, and collecting them once they have arrived.

A parcel is sent by a hero (``send``: gold from the purse, items from the pack, for their team to send) or made by the game
(``send_goods``: a reward on its way, with nothing taken from anyone). Either way it names the receiving team, the guild it is
delivered to and how long it takes, and it is in transit until the wall clock passes its arrival. Then a hero on the receiving team
collects it (``collect``): the gold goes to the team (``Economy.credit_team``) and the items through the game's storage
(``storage.service.deliver``), all or nothing, so a team with no room for every item leaves the parcel waiting.

Where a hero must stand to collect (at that guild) is the game's: it calls ``collect`` from its dialog. A game that wants a parcel
to reach its recipient whatever the sender does sends it with ``send_goods`` and cancels it (``cancel``) if the questing party
delivers first: a cancelled parcel goes back to its sender, or vanishes if the game made it.
"""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..content.models import Item
from ..economy import Economy, NotEnoughGold
from ..heroes import inventory
from ..heroes.models import Hero, TeamMember
from ..storage import service as storage_service
from ..storage.hooks import Storage
from .models import CANCELLED, COLLECTED, TRANSIT, Shipment, ShipmentItem

MAX_AMOUNT = 10**9
MAX_DELAY = 365 * 24 * 3600


class DeliveryError(ValueError):
    """Something the caller can fix: the message says what."""


def _check(guild: str, purpose: str, delay: int, gold: int, has_items: bool) -> None:
    if not guild or not purpose:
        raise DeliveryError("a parcel needs a guild and a purpose")
    if not 0 <= delay <= MAX_DELAY:
        raise DeliveryError("the delay is zero or more seconds, and at most a year")
    if not 0 <= gold <= MAX_AMOUNT:
        raise DeliveryError("gold is zero or more")
    if not (gold or has_items):
        raise DeliveryError("there is nothing to send")


async def _hero_of(session: AsyncSession, team_id: int) -> Hero:
    hero = await session.scalar(
        select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot).limit(1)
    )
    if hero is None:
        raise DeliveryError("that team has no hero")
    return hero


async def send(
    session: AsyncSession, economy: Economy, hero: Hero, to_team_id: int, guild: str, purpose: str, ref: str = "",
    delay: int = 0, gold: int = 0, items: Sequence[tuple[int, int]] = (),
) -> Shipment:
    """$hero sends their team's parcel: $gold from the hero's purse and $items, (pack position, quantity) pairs, from their pack, to
    team $to_team_id at $guild, arriving $delay seconds from now. All or nothing. Nothing worn can be sent."""
    _check(guild, purpose, delay, gold, bool(items))
    positions = [position for position, _qty in items]
    if len(set(positions)) != len(positions):
        raise DeliveryError("each stack can be named once")
    from_team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if from_team_id is None:
        raise DeliveryError("only a hero on a team can send a parcel")
    await _hero_of(session, to_team_id)
    async with session.begin_nested():  # a refusal anywhere gives back what was taken before it
        await session.execute(select(Hero.id).where(Hero.id == hero.id).with_for_update())  # two parcels at once cannot take one stack
        shipment = Shipment(
            guild=guild, purpose=purpose, ref=ref, from_team_id=from_team_id, to_team_id=to_team_id, gold=gold,
            arrives_at=wallclock.timestamp() + delay,
        )
        session.add(shipment)
        await session.flush()
        if gold:
            try:
                await economy.debit(session, hero, gold)
            except NotEnoughGold as error:
                raise DeliveryError("not enough gold") from error
        worn = set((await inventory.equipment(session, hero)).values())
        stacks = {stack.position: (stack, item) for stack, item in await inventory.stacks(session, hero)}
        for position, qty in sorted(items, reverse=True):  # from the end, so taking a stack does not move the next
            found = stacks.get(position)
            if found is None:
                raise DeliveryError("there's nothing in that position")
            stack, item = found
            if not 1 <= qty <= stack.qty:
                raise DeliveryError(f"you have {stack.qty} of that, and a parcel holds at least 1")
            if stack.id in worn:
                raise DeliveryError("take it off first")
            if await inventory.remove_item(session, hero, position, qty) != qty:
                raise DeliveryError("that is no longer there")
            session.add(ShipmentItem(shipment_id=shipment.id, item_id=item.id, qty=qty))
        await session.flush()
    return shipment


async def send_goods(
    session: AsyncSession, to_team_id: int, guild: str, purpose: str, ref: str = "", delay: int = 0,
    gold: int = 0, items: Sequence[tuple[str, int]] = (),
) -> Shipment:
    """The game sends a parcel it makes: $gold and $items, (item key, quantity) pairs, taken from no one. Raises DeliveryError,
    sending nothing, for an unknown item or a team with no hero."""
    _check(guild, purpose, delay, gold, bool(items))
    await _hero_of(session, to_team_id)
    async with session.begin_nested():
        shipment = Shipment(
            guild=guild, purpose=purpose, ref=ref, from_team_id=None, to_team_id=to_team_id, gold=gold,
            arrives_at=wallclock.timestamp() + delay,
        )
        session.add(shipment)
        await session.flush()
        for key, qty in items:
            item = await session.scalar(select(Item).where(Item.key == key))
            if item is None:
                raise DeliveryError(f"there's no item {key!r}")
            if not 1 <= qty <= inventory.MAX_ITEM_QTY:
                raise DeliveryError("a parcel holds at least 1 of each item")
            session.add(ShipmentItem(shipment_id=shipment.id, item_id=item.id, qty=qty))
        await session.flush()
    return shipment


async def contents(session: AsyncSession, shipment: Shipment) -> dict:
    """What a parcel holds: its gold and its items, as (item key, quantity) pairs."""
    rows = await session.execute(
        select(Item.key, ShipmentItem.qty).join(Item, Item.id == ShipmentItem.item_id)
        .where(ShipmentItem.shipment_id == shipment.id).order_by(ShipmentItem.id)
    )
    return {"gold": shipment.gold, "items": [(key, qty) for key, qty in rows.all()]}


async def parcels(
    session: AsyncSession, to_team_id: int | None = None, guild: str | None = None, purpose: str | None = None, ref: str | None = None,
    arrived: bool | None = None,
) -> list[Shipment]:
    """The parcels still in transit, oldest first, narrowed by whichever of team, guild, purpose and ref are given. With $arrived
    true, only those whose time has come (ready to collect); with it false, only those still on the way."""
    query = select(Shipment).where(Shipment.state == TRANSIT).order_by(Shipment.id)
    for column, value in ((Shipment.to_team_id, to_team_id), (Shipment.guild, guild), (Shipment.purpose, purpose), (Shipment.ref, ref)):
        if value is not None:
            query = query.where(column == value)
    if arrived is not None:
        now = wallclock.timestamp()
        query = query.where(Shipment.arrives_at <= now if arrived else Shipment.arrives_at > now)
    return list((await session.scalars(query)).all())


async def collect(session: AsyncSession, economy: Economy, storage: Storage, hero: Hero, shipment_id: int) -> Shipment:
    """A hero on the receiving team takes the parcel that has arrived. Gold goes to their team and items through the game's storage,
    all or nothing. Raises DeliveryError, changing nothing, if it has not arrived, is settled already, is not for this hero's team or
    does not all fit."""
    shipment = await session.get(Shipment, shipment_id)
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if shipment is None or team_id is None or shipment.to_team_id != team_id:  # the same answer, so ids cannot be guessed at
        raise DeliveryError("there is no such parcel for you")
    async with session.begin_nested():
        now = wallclock.timestamp()
        claimed = await session.execute(
            update(Shipment).where(Shipment.id == shipment_id, Shipment.state == TRANSIT, Shipment.arrives_at <= now)
            .values(state=COLLECTED, settled_at=now)
        )
        if claimed.rowcount == 0:
            raise DeliveryError("that parcel has not arrived, or was collected already")
        await _pay(session, economy, storage, shipment, hero)
    await session.refresh(shipment)
    return shipment


async def cancel(session: AsyncSession, economy: Economy, storage: Storage, shipment_id: int) -> Shipment:
    """Calls a parcel back before anyone collects it (the game's call, say because the questing party delivered first). It goes
    back to its sender, or vanishes if the game made it. Raises DeliveryError, changing nothing, if it is settled already or the
    sender has no hero or no room for every item."""
    shipment = await session.get(Shipment, shipment_id)
    if shipment is None:
        raise DeliveryError("there is no such parcel")
    async with session.begin_nested():
        claimed = await session.execute(
            update(Shipment).where(Shipment.id == shipment_id, Shipment.state == TRANSIT).values(state=CANCELLED, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            raise DeliveryError("that parcel is settled already")
        if shipment.from_team_id is not None:
            await _pay(session, economy, storage, shipment, await _hero_of(session, shipment.from_team_id))
    await session.refresh(shipment)
    return shipment


async def _pay(session: AsyncSession, economy: Economy, storage: Storage, shipment: Shipment, hero: Hero) -> None:
    """Gives the parcel to $hero's team: the gold to the team, the items to the hero's storage and packs. Raises DeliveryError if an
    item has nowhere to go (the caller's savepoint undoes what was paid)."""
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if shipment.gold:
        await economy.credit_team(session, team_id, shipment.gold)
    for key, qty in (await contents(session, shipment))["items"]:
        if (await storage_service.deliver(session, storage, hero, key, qty)).left:
            raise DeliveryError("there is no room for every item")
