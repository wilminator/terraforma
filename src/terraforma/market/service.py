"""Shopping from a conversation: the hero's NPC is waiting on a ``vend``, ``hawk`` or ``shop`` activity, and the hero buys from
its stock and sells back what they carry.

The terms come from the server: ``vend`` and ``hawk`` from the lists written in the NPC's dialog (a ``vend`` only sells, a ``hawk``
only buys), ``shop,key`` from the game's ``Market`` (both, by the area's economy level). The browser only says which item and
how many, never a price. Gold moves through the game's economy (the team's purse by default). A purchase puts what fits in the
pack and charges only for that; a sale that would pay nothing is refused, so nothing is thrown away for no gold.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..economy import Economy
from ..fights.rules import Rules
from ..heroes import inventory
from ..heroes.models import Hero, TeamMember
from ..npcs.state import DialogState
from .hooks import Market

MAX_QTY = inventory.MAX_ITEM_QTY
COMMANDS = ("vend", "hawk", "shop")


class ShopError(ValueError):
    """Something the caller can fix: the message says what."""


@dataclass
class Terms:
    """What the shop sells (items with their price) and what it pays for an item (0 pays nothing)."""

    wares: list[tuple[Item, int]]
    pays: Callable[[Item], int]
    level: int | None = None


async def _items(session: AsyncSession, keys: list[str]) -> dict[str, Item]:
    rows = await session.scalars(select(Item).where(Item.key.in_(keys), Item.active.is_(True)))
    return {item.key: item for item in rows.all()}


async def shoppers(session: AsyncSession, hero: Hero) -> list[Hero]:
    """Who the shop sees: the heroes of the teams acting with the hero's (the ethereal party in a town), or just the hero."""
    state = DialogState(session, hero)
    party = await state.party()
    if party is None:
        return [hero]
    ids = select(TeamMember.hero_id).where(TeamMember.team_id.in_(party.team_ids))
    return list((await session.scalars(select(Hero).where(Hero.id.in_(ids)).order_by(Hero.id))).all()) or [hero]


async def terms(session: AsyncSession, market: Market, rules: Rules, hero: Hero, prompt: dict) -> Terms:
    command, parts = prompt["command"], prompt["parts"]
    if command == "shop":
        shop = parts[0]
        level = max(0, int(await market.level(session, rules, shop, hero, await shoppers(session, hero))))
        stock = await market.stock(session, shop, level)
        return Terms([(item, market.price(shop, item)) for item in stock], lambda item: market.sell_price(shop, item), level)
    if command == "vend":
        pairs = list(zip(parts[0:len(parts) - len(parts) % 2:2], map(int, parts[1:len(parts) - len(parts) % 2:2])))
        found = await _items(session, [key for key, _price in pairs])
        return Terms([(found[key], price) for key, price in pairs if key in found], lambda item: 0)
    margin = int(parts[0])  # hawk: a listed item at its listed price, any other at its own price by the margin
    rest = parts[1:]
    listed = {key: int(price) for key, price in zip(rest[0:len(rest) - len(rest) % 2:2], rest[1:len(rest) - len(rest) % 2:2])}
    return Terms([], lambda item: listed.get(item.key, (item.price * margin + 50) // 100))


async def view(session: AsyncSession, market: Market, rules: Rules, economy: Economy, hero: Hero, prompt: dict) -> dict:
    """What the hero sees: the wares with prices, what they could sell and for how much each, and their gold."""
    seen = await terms(session, market, rules, hero, prompt)
    sellable = []
    for stack, item in await inventory.stacks(session, hero):
        if (each := seen.pays(item)) > 0:
            sellable.append({"position": stack.position, "item": item.key, "name": item.name, "qty": stack.qty, "each": each})
    return {
        "command": prompt["command"],
        "level": seen.level,
        "gold": await economy.balance(session, hero),
        "wares": [{"item": item.key, "name": item.name, "price": price, "stacks": inventory.stackable(item)} for item, price in seen.wares],
        "sellable": sellable,
    }


async def buy(session: AsyncSession, market: Market, rules: Rules, economy: Economy, hero: Hero, prompt: dict, item_key: str, qty: int) -> dict:
    """The hero buys $qty of an item on sale now. Only what fits in the pack is charged for; none fitting is refused. Equipment
    is bought one at a time (it does not stack). Returns what happened and the hero's gold."""
    seen = await terms(session, market, rules, hero, prompt)
    ware = next(((item, price) for item, price in seen.wares if item.key == item_key), None)
    if ware is None:
        raise ShopError("that isn't for sale")
    item, price = ware
    if qty < 1 or qty > MAX_QTY or (qty > 1 and not inventory.stackable(item)):
        raise ShopError("how many?")
    async with session.begin_nested():  # a refused purchase puts nothing in the pack
        left = await inventory.add_item(session, hero, item.key, qty)
        bought = qty - left
        if bought == 0:
            raise ShopError(f"{hero.name} can't carry any more")
        try:
            await economy.debit(session, hero, price * bought)
        except ValueError as error:  # NotEnoughGold
            raise ShopError("that costs more gold than there is") from error
    return {"bought": bought, "not_fitting": left, "cost": price * bought, "gold": await economy.balance(session, hero)}


async def sell(session: AsyncSession, market: Market, rules: Rules, economy: Economy, hero: Hero, prompt: dict, position: int, qty: int) -> dict:
    """The hero sells $qty from the stack at $position. A sale that would pay nothing is refused."""
    seen = await terms(session, market, rules, hero, prompt)
    found = next(((stack, item) for stack, item in await inventory.stacks(session, hero) if stack.position == position), None)
    if found is None:
        raise ShopError(f"{hero.name} doesn't have that")
    stack, item = found
    if qty < 1 or qty > stack.qty:
        raise ShopError("how many?")
    each = seen.pays(item)
    if each < 1:
        raise ShopError(f"the shop won't pay anything for {item.name}")
    sold = await inventory.remove_item(session, hero, position, qty)
    await economy.credit(session, hero, each * sold)
    return {"sold": sold, "paid": each * sold, "gold": await economy.balance(session, hero)}
