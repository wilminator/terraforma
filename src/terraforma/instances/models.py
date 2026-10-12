"""A quest instance, and the parties inside it."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class QuestInstance(Timestamps, Base):
    """One run of a quest on a map: the parties inside it see only each other. A public quest has no instance."""

    __tablename__ = "quest_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The quest's key, the game's own (the same text a quest marker uses).
    quest: Mapped[str] = mapped_column(String(64), index=True)
    #: ``private`` (one party) or ``shared`` (several parties, which may cooperate or compete).
    mode: Mapped[str] = mapped_column(String(16))
    map_id: Mapped[int] = mapped_column(ForeignKey("maps.id"), index=True)
    #: The most parties a shared instance takes (1 for a private one).
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    #: Until when another party may join (a shared instance; the wall clock).
    joinable_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: ``open`` until a party completes the quest, then ``won``: the others may stay and explore but cannot complete it.
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    #: The party that completed the quest. No link: the party may be gone by now.
    winner_party_id: Mapped[int | None] = mapped_column(Integer)


class InstanceParty(Timestamps, Base):
    """A party inside an instance (a party is in at most one), and where it came from, so leaving puts it back."""

    __tablename__ = "instance_parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("quest_instances.id"), index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    from_map_id: Mapped[int] = mapped_column(ForeignKey("maps.id"))
    from_x: Mapped[int] = mapped_column(Integer)
    from_y: Mapped[int] = mapped_column(Integer)
