"""What a game decides about shops. A game subclasses ``Market`` and hands it to ``Game(market=...)``.

A shop's stock is a function of its place's *economy level*, which is the same thing as how hard the region is: better
gear for harder regions, with regional flavours (katanas here, scimitars there). So there is no seed for a shop's stock;
the game's ``Market`` says what a shop sells and for how much. By default the level is the party's PXP and a shop sells
every active item priced at or under it, the cheapest first; selling an item back pays 75% of its price. This is a public
interface (the license exception covers it): the method names and signatures are what games build on.

    class Regional(terraforma.market.hooks.Market):
        async def stock(self, session, shop, level):
            if shop.key == "eastern-smith":
                return [item for item in await super().stock(session, shop, level) if item.key.startswith("katana")]
            return await super().stock(session, shop, level)

        def sell_percent(self, shop, item):
            return 200 if item.key == "healing-herb" else 75  # a quest: the area needs healing herbs
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..fights.rules import Rules
from ..heroes.models import Hero
from .models import Shop


class Market:
    #: What selling an item back pays, in percent of its price, unless ``sell_percent`` says otherwise.
    sell_back_percent: int = 75

    async def level(self, session: AsyncSession, rules: Rules, shop: Shop, hero: Hero, party: list[Hero]) -> int:
        """The shop's economy level for $hero, who is shopping with $party (every hero of their party, or of their team if
        they are in no party, or just themselves). Their total PXP by default (``Rules.pxp``: how strong they are as one
        number). A game overrides it to make the level a property of the place, with no regard to who is asking."""
        from ..fights.build import hero_fighter  # here, since fights reads the heroes

        total = 0
        for member in party:
            total += rules.pxp(await hero_fighter(session, member))
        return total

    async def stock(self, session: AsyncSession, shop: Shop, level: int) -> list[Item]:
        """The items the shop sells at economy $level, in the order shown. By default every active item with a price,
        priced at or under the level, cheapest first and then by name."""
        rows = await session.scalars(
            select(Item).where(Item.active.is_(True), Item.price > 0, Item.price <= level).order_by(Item.price, Item.name)
        )
        return list(rows.all())

    def price(self, shop: Shop, item: Item) -> int:
        """What the shop charges for one $item. The item's own price by default."""
        return item.price

    def sell_percent(self, shop: Shop, item: Item) -> int:
        """What the shop pays for one $item back, in percent of its price (``sell_back_percent`` by default). A game can
        override it for one item, so a quest ('the area needs healing herbs') makes the economy the reward."""
        return self.sell_back_percent

    def sell_price(self, shop: Shop, item: Item) -> int:
        """What the shop pays for one $item back, in gold, rounding half up. Nothing pays less than 0."""
        return max(0, (self.price(shop, item) * self.sell_percent(shop, item) + 50) // 100)
