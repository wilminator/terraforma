"""Who follows whom, and who has barred whom from their profile."""

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class Follow(Timestamps, Base):
    """$follower follows $followed: one way, instant, and private to the follower. ``share_back`` is the follower's choice
    to show the followed player their handle and profile link (off until they switch it on)."""

    __tablename__ = "follows"

    follower_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    followed_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False, index=True)
    share_back: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ProfileBan(Timestamps, Base):
    """$owner has barred $banned from their profile. It overrides a follow of the owner by the banned player and does
    nothing in a game."""

    __tablename__ = "profile_bans"

    owner_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    banned_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False, index=True)
