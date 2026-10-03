"""Shopping: a hero standing on a shop's tile buys from its stock and sells back what they carry.

Gold moves through the game's economy (the team's purse by default), so a purchase takes from, and a sale pays into,
whatever purse the hero spends from. The server decides the stock and every price (``Market``), never the client. A hero
in a fight that is still running cannot shop. A purchase puts what fits in the pack and charges only for that; a sale
that would pay nothing is refused, so nothing is thrown away for no gold.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..economy import Economy
from ..fights.rules import Rules
from ..heroes import inventory
from ..heroes.field import in_running_fight
from ..heroes.models import Hero, TeamMember
from ..parties import service as parties
from .hooks import Market
from .models import Shop

MAX_QTY = inventory.MAX_ITEM_QTY


class ShopError(ValueError):
    """Something the caller can fix: the message says what."""


class NoSuchShop(ShopError):
    """There is no such shop where the hero stands."""


async def place_shop(session: AsyncSession, key: str, name: str, map_id: int, x: int, y: int) -> Shop:
    """Stands a shop on a map at a tile (a game's setup code calls it). The shop with this $key is moved and renamed if it
    exists already, so setup can run again."""
    shop = await session.scalar(select(Shop).where(Shop.key == key))
    if shop is None:
        shop = Shop(key=key, name=name, map_id=map_id, x=x, y=y)
        session.add(shop)
    else:
        shop.name, shop.map_id, shop.x, shop.y = name, map_id, x, y
    await session.flush()
    return shop


async def shops_here(session: AsyncSession, hero: Hero) -> list[Shop]:
    """The shops on the tile the hero stands on."""
    rows = await session.scalars(select(Shop).where(Shop.map_id == hero.map_id, Shop.x == hero.x, Shop.y == hero.y).order_by(Shop.name, Shop.id))
    return list(rows.all())


async def shop_here(session: AsyncSession, hero: Hero, shop_id: int) -> Shop:
    shop = await session.get(Shop, shop_id)
    if shop is None or (shop.map_id, shop.x, shop.y) != (hero.map_id, hero.x, hero.y):
        raise NoSuchShop("there's no such shop here")
    return shop


async def shoppers(session: AsyncSession, hero: Hero) -> list[Hero]:
    """Who the shop sees: the hero's whole party, or the hero's team if they are in no party, or just the hero."""
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    party = await parties.party_of(session, team_id) if team_id is not None else None
    if party is not None:
        ids = await parties.hero_ids(session, party.id)
    elif team_id is not None:
        ids = list((await session.scalars(select(TeamMember.hero_id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot))).all())
    else:
        ids = [hero.id]
    found = {each.id: each for each in (await session.scalars(select(Hero).where(Hero.id.in_(ids)))).all()}
    return [found[hero_id] for hero_id in ids if hero_id in found]


async def level_of(session: AsyncSession, market: Market, rules: Rules, shop: Shop, hero: Hero) -> int:
    return max(0, int(await market.level(session, rules, shop, hero, await shoppers(session, hero))))


async def _check_can_shop(session: AsyncSession, hero: Hero) -> None:
    if await in_running_fight(session, hero):
        raise ShopError(f"{hero.name} is in a fight")


async def view(session: AsyncSession, market: Market, rules: Rules, economy: Economy, hero: Hero, shop_id: int) -> dict:
    """What the hero sees in the shop: the stock with prices, what they could sell and for how much each, and their gold."""
    shop = await shop_here(session, hero, shop_id)
    level = await level_of(session, market, rules, shop, hero)
    return {
        "shop": {"id": shop.id, "key": shop.key, "name": shop.name},
        "level": level,
        "gold": await economy.balance(session, hero),
        "wares": [
            {"item": item.key, "name": item.name, "price": market.price(shop, item), "stacks": inventory.stackable(item)}
            for item in await market.stock(session, shop, level)
        ],
        "sellable": [
            {"position": stack.position, "item": item.key, "name": item.name, "qty": stack.qty, "each": market.sell_price(shop, item)}
            for stack, item in await inventory.stacks(session, hero)
        ],
    }


async def buy(session: AsyncSession, market: Market, rules: Rules, economy: Economy, hero: Hero, shop_id: int, item_key: str, qty: int) -> dict:
    """The hero buys $qty of an item the shop sells now. Only what fits in the pack is charged for; none fitting is refused.
    Equipment is bought one at a time (it does not stack). Returns what happened and the hero's gold."""
    shop = await shop_here(session, hero, shop_id)
    await _check_can_shop(session, hero)
    level = await level_of(session, market, rules, shop, hero)
    item = next((each for each in await market.stock(session, shop, level) if each.key == item_key), None)
    if item is None:
        raise ShopError("that isn't for sale")
    if qty < 1 or qty > MAX_QTY or (qty > 1 and not inventory.stackable(item)):
        raise ShopError("how many?")
    price = market.price(shop, item)
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


async def sell(session: AsyncSession, market: Market, economy: Economy, hero: Hero, shop_id: int, position: int, qty: int) -> dict:
    """The hero sells $qty from the stack at $position. A sale that would pay nothing is refused."""
    shop = await shop_here(session, hero, shop_id)
    await _check_can_shop(session, hero)
    found = next(((stack, item) for stack, item in await inventory.stacks(session, hero) if stack.position == position), None)
    if found is None:
        raise ShopError(f"{hero.name} doesn't have that")
    stack, item = found
    if qty < 1 or qty > stack.qty:
        raise ShopError("how many?")
    each = market.sell_price(shop, item)
    if each < 1:
        raise ShopError(f"the shop won't pay anything for {item.name}")
    sold = await inventory.remove_item(session, hero, position, qty)
    await economy.credit(session, hero, each * sold)
    return {"sold": sold, "paid": each * sold, "gold": await economy.balance(session, hero)}
