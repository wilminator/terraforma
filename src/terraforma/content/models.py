"""The content tables. Rows come from a game's seed files and are written only by content.loader.

A row the seed no longer lists is kept but marked inactive, never deleted:
heroes, fights and history may still point at it. References between
rows (a job's abilities, a monster's items) are lists of keys inside JSON
columns, checked when the seed is loaded.
"""

from sqlalchemy import JSON, Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class ContentRow:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The game's own name for this row, stable across releases of the game.
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Ability(ContentRow, Base):
    __tablename__ = "abilities"

    kind: Mapped[str] = mapped_column(String(16))  # "spell" or "skill"
    mp_cost: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(255), default="")
    icon: Mapped[str] = mapped_column(String(64), default="")
    effect: Mapped[dict] = mapped_column(JSON)
    presentation: Mapped[dict] = mapped_column(JSON)


class Item(ContentRow, Base):
    __tablename__ = "items"

    price: Mapped[int] = mapped_column(Integer, default=0)
    one_use: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    icon: Mapped[str] = mapped_column(String(64), default="")
    use_effect: Mapped[dict | None] = mapped_column(JSON)
    # Slots the item takes when equipped (null: it can't be equipped).
    equip_slots: Mapped[list | None] = mapped_column(JSON)
    stat_bonus: Mapped[dict] = mapped_column(JSON)
    stat_percent: Mapped[dict] = mapped_column(JSON)
    attack: Mapped[dict | None] = mapped_column(JSON)
    use_presentation: Mapped[dict] = mapped_column(JSON)
    fight_presentation: Mapped[dict] = mapped_column(JSON)


class Job(ContentRow, Base):
    __tablename__ = "jobs"

    xp_needed: Mapped[int] = mapped_column(Integer, default=0)
    # Stats gained per level.
    stat_growth: Mapped[dict] = mapped_column(JSON)
    abilities: Mapped[list] = mapped_column(JSON)


class Personality(ContentRow, Base):
    """How a fighter looks and sounds: its animations in a fight and walking around."""

    __tablename__ = "personalities"

    animations: Mapped[dict] = mapped_column(JSON)
    overworld: Mapped[dict] = mapped_column(JSON)


class Monster(ContentRow, Base):
    __tablename__ = "monsters"

    personality: Mapped[str] = mapped_column(String(64))
    xp_reward: Mapped[int] = mapped_column(Integer, default=0)
    gold_reward: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON)
    abilities: Mapped[list] = mapped_column(JSON)
    items: Mapped[list] = mapped_column(JSON)
    equipment: Mapped[list] = mapped_column(JSON)
    ai: Mapped[dict] = mapped_column(JSON)


class Status(ContentRow, Base):
    """A status fighters can be under (see fights.status): its ticks, modifiers, duration and intensity."""

    __tablename__ = "statuses"

    kind: Mapped[str] = mapped_column(String(8))  # "good" or "bad"
    description: Mapped[str] = mapped_column(String(255), default="")
    icon: Mapped[str] = mapped_column(String(64), default="")
    duration: Mapped[int | None] = mapped_column(Integer)
    intensity: Mapped[dict] = mapped_column(JSON)
    ticks: Mapped[list] = mapped_column(JSON)
    modifiers: Mapped[list] = mapped_column(JSON)
    xp_share: Mapped[float] = mapped_column(Float, default=0.0)
