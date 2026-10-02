"""A fight, who is in it, and the log of what happened.

A fight is stored as DragonStar stores it: one *initial state* snapshot, written
once when the fight starts, and an append-only log of rounds. The fight now is
the snapshot with every round's events applied in order (fights.replay), so
nothing is ever stored twice and any moment of a fight can be rebuilt. Each
round also records the commands that led to it, so it can be played again from
the dice to check it (fights.store.verify), and a hash of the round before it,
so a changed or missing round shows (a chain of hashes: tamper-evident, nothing
more is needed because the server is the only authority). The snapshots, commands and events are
stored as exact text (``ExactJSON``), because a database's own JSON type may re-format a float and
a hash covers every digit.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..db.dialect import ExactJSON
from ..world.location import Located


class FightRecord(Located, Timestamps, Base):
    """A fight, standing on a map. ``guid`` is the public name for sharing a fight to watch."""

    __tablename__ = "fights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str] = mapped_column(String(32), unique=True)
    # The fight as it began, as fights.state.dehydrate writes it. Written once, never changed.
    initial_state: Mapped[dict] = mapped_column(ExactJSON)
    # Whether the fight's gold has been paid out (it is paid once: see terraforma.economy).
    gold_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Live fights: when the round being waited for plays whether or not everyone has committed (None: not
    # running), and whether the fight has ended and been saved (see fights.live).
    round_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    finished: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Whether the items the fight dropped have been put in the heroes' inventories (once: see fights.store).
    drops_saved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class FightParticipant(Base):
    """Who is fighting where: a fighter's address and whether it is a hero or a monster. Fixed when the fight starts."""

    __tablename__ = "fight_participants"
    __table_args__ = (UniqueConstraint("fight_id", "party", "group_index", "character"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), index=True)
    party: Mapped[int] = mapped_column(Integer)
    group_index: Mapped[int] = mapped_column(Integer)
    character: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    hero_id: Mapped[int | None] = mapped_column(ForeignKey("heroes.id"), index=True)
    monster_key: Mapped[str | None] = mapped_column(String(64))


class FightActionRecord(Base):
    """One round of a fight: the commands, the events they produced, and the chain hash."""

    __tablename__ = "fight_actions"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    commands: Mapped[list] = mapped_column(ExactJSON)
    events: Mapped[list] = mapped_column(ExactJSON)
    # The hash of the round before (of the initial state, for the first), and this round's own.
    previous_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    # The fight after the round, kept only when asked (to check replays against).
    final_state: Mapped[dict | None] = mapped_column(ExactJSON)


class FightCommandRecord(Base):
    """A fighter's command for the round being waited for. Taken from the player, replaced if they change their mind,
    and gone once the round has played (the round's own record keeps the commands that were used)."""

    __tablename__ = "fight_commands"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
    round_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    party: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    group_index: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    character: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    command: Mapped[int] = mapped_column(Integer)
    using_index: Mapped[int] = mapped_column(Integer)
    target: Mapped[list] = mapped_column(JSON)
