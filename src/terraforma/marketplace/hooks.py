"""What a game decides about its marketplace: how long a listing lasts, what posting one costs and what getting a purchase to the
buyer's guild costs. A game subclasses ``Marketplace`` and hands it to ``Game(marketplace=...)``. This is a public interface (the
license exception covers it): the method names and signatures are what games build on.

The amounts here are *base* prices. What a team really pays is the base put through the game's fee rule (``Game(fees=...)``,
``guild.fees``): a higher rank in the guild, or a merchant guild, pays less.

    class Bazaar(terraforma.marketplace.hooks.Marketplace):
        def delivery(self, from_guild, to_guild):
            return Route(fee=0, delay=0) if from_guild == to_guild else Route(fee=25, delay=6 * 3600)
"""

from dataclasses import dataclass

DAY = 24 * 3600


@dataclass(frozen=True)
class Route:
    """What it takes to bring a purchase from the guild it was listed at to the buyer's guild: a base fee in gold and the seconds the
    parcel is on its way."""

    fee: int = 0
    delay: int = 0


class Marketplace:
    def lifetime(self, guild: str) -> int:
        """How many seconds a listing posted at $guild stays open. A week, by default."""
        return 7 * DAY

    def listing_fee(self, guild: str, price_gold: int, price_tokens: int) -> int:
        """The base gold it costs to post a listing at $guild for that price. A twentieth of the gold price (at least 1 when there is a
        gold price), by default."""
        return max(1, price_gold // 20) if price_gold else 0

    def delivery(self, from_guild: str, to_guild: str) -> Route:
        """The route from the guild a listing is at to the guild the buyer wants the goods at. Free and at once within a guild, 10 gold
        and an hour between two, by default."""
        return Route() if from_guild == to_guild else Route(fee=10, delay=3600)
