"""What a game decides about shops. A game subclasses ``Market`` and hands it to ``Game(market=...)``.

A shop is a dialog activity (``vend`` and ``hawk`` write their own item and price lists in the NPC's text; ``shop,key`` asks
this ``Market``). A ``shop``'s stock is a function of its place's *economy level*, which is the same thing as how hard the region
is: better gear for harder regions, with regional flavours (katanas here, scimitars there). So there is no seed for a shop's
stock; the game's ``Market`` says what the shop with a key sells and for how much. By default the level is the acting party's
PXP and a shop sells every active item priced at or under it, the cheapest first; selling an item back pays 75% of its price.
A game can tie an item to a settlement's supply of a material (``supplied``): the shop then charges more as the supply
falls (``price_percent``) and stops selling the item below a threshold. Nothing is tied by default.
This is a public interface (the license exception covers it): the method names and signatures are what games build on.

    class Regional(terraforma.market.hooks.Market):
        async def stock(self, session, shop, level):
            if shop == "eastern-smith":
                return [item for item in await super().stock(session, shop, level) if item.key.startswith("katana")]
            return await super().stock(session, shop, level)

        def sell_percent(self, shop, item):
            return 200 if item.key == "healing-herb" else 75  # a quest: the area needs healing herbs

    class Smithy(terraforma.market.hooks.Market):
        async def supplied(self, session, shop, item):
            if shop == "eastern-smith" and item.key.startswith("katana"):
                return Supplied("eastern-town", "steel")  # the settlement's key and the material's
            return None

        def price_percent(self, shop, item, fullness):
            return None if fullness < 25 else 100 + (100 - fullness)  # sells out below 25%, 100% to 175% above
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..fights.rules import Rules
from ..heroes.models import Hero


@dataclass(frozen=True)
class Supplied:
    """The supply an item depends on: the key of a settlement (``settlements.json``) and the key of a material it keeps."""

    settlement: str
    material: str


class Market:
    #: What selling an item back pays, in percent of its price, unless ``sell_percent`` says otherwise.
    sell_back_percent: int = 75

    async def level(self, session: AsyncSession, rules: Rules, shop: str, hero: Hero, party: list[Hero]) -> int:
        """The economy level of the shop with key $shop for $hero, who shops with $party (every hero of the teams acting with
        theirs, or just themselves). Their total PXP by default (``Rules.pxp``: how strong they are as one number). A game
        overrides it to make the level a property of the place, with no regard to who is asking."""
        from ..fights.build import hero_fighter  # here, since fights reads the heroes

        total = 0
        for member in party:
            total += rules.pxp(await hero_fighter(session, member, rules))
        return total

    async def stock(self, session: AsyncSession, shop: str, level: int) -> list[Item]:
        """The items the shop sells at economy $level, in the order shown. By default every active item with a price, priced at
        or under the level, cheapest first and then by name."""
        rows = await session.scalars(
            select(Item).where(Item.active.is_(True), Item.price > 0, Item.price <= level).order_by(Item.price, Item.name)
        )
        return list(rows.all())

    def price(self, shop: str, item: Item) -> int:
        """What the shop charges for one $item. The item's own price by default."""
        return item.price

    async def supplied(self, session: AsyncSession, shop: str, item: Item) -> Supplied | None:
        """The supply the $item at the shop depends on, or None (the default) when it does not depend on one."""
        return None

    #: Below this fullness (percent of the supply's capacity) the default curve starts to raise prices.
    dear_below: int = 50
    #: Below this fullness the default curve sells the item no more.
    sold_out_below: int = 10
    #: The price, in percent, just above ``sold_out_below``.
    dearest_percent: int = 200

    def price_percent(self, shop: str, item: Item, fullness: int) -> int | None:
        """What the shop charges for a supplied $item, in percent of ``price``, when its supply is $fullness percent of its
        capacity (0 to 100); None means sold out. By default 100 from ``dear_below`` up, rising in a line to
        ``dearest_percent`` at ``sold_out_below``, and sold out under it."""
        if fullness >= self.dear_below:
            return 100
        if fullness < self.sold_out_below:
            return None
        span = max(1, self.dear_below - self.sold_out_below)
        return self.dearest_percent - (self.dearest_percent - 100) * (fullness - self.sold_out_below) // span

    def sell_percent(self, shop: str, item: Item) -> int:
        """What the shop pays for one $item back, in percent of its price (``sell_back_percent`` by default). A game can
        override it for one item, so a quest ('the area needs healing herbs') makes the economy the reward."""
        return self.sell_back_percent

    def sell_price(self, shop: str, item: Item) -> int:
        """What the shop pays for one $item back, in gold, rounding half up. Nothing pays less than 0."""
        return max(0, (self.price(shop, item) * self.sell_percent(shop, item) + 50) // 100)
