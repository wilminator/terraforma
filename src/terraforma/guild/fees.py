"""What a guild charges a team: the game's fee rule.

A guild's services (a contract, a marketplace listing, a delivery) have a base price the game sets, and what a team actually pays
depends on who it is: a team that has risen in that guild pays less, and a merchant guild takes a further cut off. So the engine
asks a ``Fees`` (``Game(fees=...)``) to turn a base price into the price for one team at one guild, and never decides the numbers.

``Fees`` itself charges the base price to everyone. ``RankFees`` is the stock rule: a percentage off for each rank the team holds in
that guild (``rank_discounts``, by rank number, the last one repeating for higher ranks), then a further percentage off at a
merchant guild (``merchant_guilds``, by guild name). A game subclasses ``Fees`` for anything else:

    class Haggling(Fees):
        async def quote(self, session, team_id, guild, base, kind=""):
            return base if kind == "contract" else base // 2

The price is whole gold, rounded up, never below zero and never above the base. This is a public interface (the license exception
covers it).
"""

from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from . import ranks


class Fees:
    async def quote(self, session: AsyncSession, team_id: int, guild: str, base: int, kind: str = "") -> int:
        """What team $team_id pays $guild for a service whose base price is $base. $kind names the service ("contract", "listing",
        "delivery": the game's own words) for a rule that prices them differently. The base price, by default."""
        return base


def _percent(value: int, what: str) -> int:
    if not 0 <= value <= 100:
        raise ValueError(f"{what} is a percentage from 0 to 100")
    return value


class RankFees(Fees):
    """A percentage off by the team's rank in the guild, and a further percentage off at a merchant guild."""

    def __init__(self, rank_discounts: Sequence[int] = (), merchant_guilds: Mapping[str, int] | None = None):
        self.rank_discounts = tuple(_percent(value, "a rank discount") for value in rank_discounts)
        self.merchant_guilds = {guild: _percent(value, "a merchant discount") for guild, value in (merchant_guilds or {}).items()}

    async def quote(self, session: AsyncSession, team_id: int, guild: str, base: int, kind: str = "") -> int:
        if base < 0:
            raise ValueError("a price is zero or more")
        rank = (await ranks.standing(session, team_id, guild))[1]
        by_rank = self.rank_discounts[min(rank, len(self.rank_discounts) - 1)] if self.rank_discounts else 0
        merchant = self.merchant_guilds.get(guild, 0)
        return -(-base * (100 - by_rank) * (100 - merchant) // 10_000)  # rounded up, in whole gold
