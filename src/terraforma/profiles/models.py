"""Profile tables. A page's address is a random token, so nobody can guess one, and the owner can replace it if it leaks.

None of these rows holds a username or an email, and the public calls never show an account id."""

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

BIO_MAX = 500
TOKEN_MAX = 32


class PlayerProfile(Base):
    """A player's page: the handle (from the account) and a short bio. ``team_pages`` is the one switch that turns
    the player's team pages on or off; each team's ``listed`` flag decides whether the player's page names it.
    ``team_alliances`` is a second switch: whether a team's public page names the alliances the team is in (off by
    default, since the page's address can be passed around)."""

    __tablename__ = "player_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    bio: Mapped[str] = mapped_column(String(BIO_MAX), default="", server_default="")
    team_pages: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    team_alliances: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class TeamProfile(Base):
    """A team's page address, and whether the player's page names the team."""

    __tablename__ = "team_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    listed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class AllianceProfile(Base):
    """An alliance's page: a short description, written by a team whose role may speak for the alliance."""

    __tablename__ = "alliance_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), unique=True)
    token: Mapped[str] = mapped_column(String(TOKEN_MAX), unique=True)
    bio: Mapped[str] = mapped_column(String(BIO_MAX), default="", server_default="")
