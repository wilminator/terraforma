"""Voice rooms: the room a channel has at the voice provider (a Discord voice channel), made when the first member asks."""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class VoiceRoom(Timestamps, Base):
    """The voice room of a chat channel (a kind and a key, as ``chat`` has them). ``provider_id`` is the provider's own name for the room.
    No link to anything else: the chat channel may be a party that no longer exists, and housekeeping closes the room when it is old."""

    __tablename__ = "voice_rooms"
    __table_args__ = (UniqueConstraint("kind", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(64))
