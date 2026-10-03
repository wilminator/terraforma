"""A party is a collection of whole teams. It stands on a map, like everything that exists somewhere."""

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located


class Party(Located, Timestamps, Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The player (account) who leads the party: the one who founded it, then whoever accepts another party into theirs.
    #: No link, like a relationship's sides; ``parties.service.leader_account`` says who leads when this player no longer
    #: has a team in the party.
    leader_account_id: Mapped[int | None] = mapped_column(Integer, index=True)
    #: Whether the party is looking for members: the guild's search lists open parties, and only those can be asked to take a team.
    open: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class PartyTeam(Base):
    """A team's place in a party. A team is in at most one party; ``position`` is the order the teams joined (0, 1, 2, ...)."""

    __tablename__ = "party_teams"
    __table_args__ = (UniqueConstraint("party_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True)
    position: Mapped[int] = mapped_column(Integer)


class PartyRequest(Timestamps, Base):
    """A team's request to join a party, until the party's leader answers it (or the team or the party goes)."""

    __tablename__ = "party_requests"
    __table_args__ = (UniqueConstraint("party_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
