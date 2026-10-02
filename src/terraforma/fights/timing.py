"""Longer rounds, as a player's own setting, and what they cost.

A player who needs more time to decide (an accessibility setting) may ask for rounds ``multiplier`` times as long, from the
choices the game offers (``Rules.time_multipliers``). The cost is on the monsters they face: each gets extra max HP, so
the fight is harder and pays more experience (a monster's PXP follows its stats). A fight has one round length, the
longest anyone in it asked for. Whoever starts a fight reads the players' settings (``multiplier_for``), builds the
fight's multiplier with ``fight_multiplier`` and makes the monsters tougher with ``toughen``.
"""

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from .combatant import Combatant
from .models import PlayerSettings
from .rules import Rules


class NotOffered(ValueError):
    """The game doesn't offer that round length."""


def offered(rules: Rules) -> list[float]:
    return [multiplier for multiplier, _bonus in rules.time_multipliers]


def fight_multiplier(multipliers: Iterable[float]) -> float:
    """The round length multiplier of a fight: the longest anyone in it asked for (1 when no one did)."""
    return max([1.0, *multipliers])


def boosted_hp(base: int, bonus: float) -> int:
    """$base max HP with $bonus more (0.05 is 5%), in whole percents and rounded up: 200 at 10% is exactly 220."""
    percent = 100 + round(bonus * 100)
    return (base * percent + 99) // 100


def toughen(monsters: Iterable[Combatant], rules: Rules, bonus: float) -> None:
    """Gives every monster $bonus more max HP, and the same share of the new maximum that it had of the old."""
    if bonus <= 0:
        return
    vital = rules.vital
    for monster in monsters:
        old, now = monster.base[vital], monster.current[vital]
        new = boosted_hp(old, bonus)
        monster.current[vital] = (now * new + old - 1) // old if old > 0 else new
        monster.base[vital] = new


async def multiplier_for(session: AsyncSession, account_id: int) -> float:
    """What the account chose (1 if it never did)."""
    settings = await session.get(PlayerSettings, account_id)
    return settings.time_multiplier if settings else 1.0


async def choose(session: AsyncSession, rules: Rules, account_id: int, multiplier: float) -> None:
    """Saves the account's choice. Raises NotOffered for a length the game doesn't offer."""
    if multiplier not in offered(rules):
        raise NotOffered(f"rounds can be {', '.join(f'{each:g}' for each in offered(rules))} times as long")
    await upsert(session, PlayerSettings.__table__, {"account_id": account_id, "time_multiplier": multiplier}, ["account_id"])
