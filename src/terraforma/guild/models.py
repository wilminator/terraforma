"""A team's standing with a guild: the points it has earned there and the rank it holds. Standing belongs to the team, like quests."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class GuildStanding(Timestamps, Base):
    """What a team has with one guild. No row means no points and rank 0. The guild is a name the game picks; the engine knows no
    guilds, only the standing of teams with the names the dialogs use."""

    __tablename__ = "guild_standing"
    __table_args__ = (UniqueConstraint("team_id", "guild"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    guild: Mapped[str] = mapped_column(String(64))
    points: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
