"""A party is a collection of whole teams. It stands on a map, like everything that exists somewhere."""

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located


class Party(Located, Timestamps, Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class PartyTeam(Base):
    """A team's place in a party. A team is in at most one party; ``position`` is the order the teams joined (0, 1, 2, ...)."""

    __tablename__ = "party_teams"
    __table_args__ = (UniqueConstraint("party_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True)
    position: Mapped[int] = mapped_column(Integer)
