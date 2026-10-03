"""Profile tables. A page's address is a random token, so nobody can guess one, and the owner can replace it if it leaks.

None of these rows holds a username or an email, and the public calls never show an account id."""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

BIO_MAX = 500
TOKEN_MAX = 32


class PlayerProfile(Base):
    """A player's page: the handle (from the account) and a short bio. Which of the player's teams are seen is each
    team's own ``visible`` flag. ``directory`` is the player's opt-in to the directory: while it is on, the player's
    handle appears in the rosters of their teams' alliances, and alliance members can go from a team's page to the
    player's page. It is off by default."""

    __tablename__ = "player_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    bio: Mapped[str] = mapped_column(String(BIO_MAX), default="", server_default="")
    directory: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class TeamProfile(Base):
    """A team's page address, and whether the team is visible: a visible team's page can be reached from the player's page
    and from its alliances' pages; a hidden one appears nowhere. Hidden until the player says otherwise."""

    __tablename__ = "team_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class AllianceProfile(Base):
    """An alliance's page: a short description, written by a team whose role may speak for the alliance."""

    __tablename__ = "alliance_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    bio: Mapped[str] = mapped_column(String(BIO_MAX), default="", server_default="")
