"""Chat tables: the messages, who has muted whom, and the flags players raise against a message.

A message belongs to a channel, which is a kind (``party``, ``alliance``, ``nearby``...) and a key text that kind understands.
Nothing here links to a party or an alliance: any kind of channel, including a game's own, fits the same table."""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

BODY_MAX = 500
NOTE_MAX = 280
OPEN, ACTIONED, DISMISSED = "open", "actioned", "dismissed"


class ChatMessage(Timestamps, Base):
    """One line said in a channel. ``map_id``, ``x`` and ``y`` are where the speaker stood (none when they stood nowhere),
    so a channel that depends on distance can ask without the engine knowing about it."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_channel", "kind", "key", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(40))
    sender_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    body: Mapped[str] = mapped_column(String(BODY_MAX))
    map_id: Mapped[int | None] = mapped_column(Integer)
    x: Mapped[int | None] = mapped_column(Integer)
    y: Mapped[int | None] = mapped_column(Integer)


class ChatMute(Base):
    """``account_id`` does not see what ``muted_id`` says. The mute follows the player, not the channel."""

    __tablename__ = "chat_mutes"
    __table_args__ = (UniqueConstraint("account_id", "muted_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    muted_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))


class ChatFlag(Timestamps, Base):
    """A player's report of a message (for the game's terms of use, or another reason it allows) for an admin to settle.
    A flagged message is kept past the retention so the admin can still read it."""

    __tablename__ = "chat_flags"
    __table_args__ = (UniqueConstraint("message_id", "reporter_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    reason: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(NOTE_MAX), default="", server_default="")
    state: Mapped[str] = mapped_column(String(10), default=OPEN, server_default=OPEN, index=True)
    resolved_by: Mapped[int | None] = mapped_column(Integer)
