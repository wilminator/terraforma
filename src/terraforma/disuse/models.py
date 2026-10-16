"""What force disuse keeps: the entries set aside with a hero, and the scene a hero is in the middle of."""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

JOB, ABILITY, ITEM = "job", "ability", "item"


class SetAside(Timestamps, Base):
    """Something a hero has had to stop using, kept with it: an ability it knew (``qty`` 1), items it carried (``qty`` of them) or the job
    it had (a record only: a hero keeps the job it chose). ``key`` is the content row's key. An ability or item comes back to the hero
    (``service.restore``) once its row is no longer disused."""

    __tablename__ = "hero_set_aside"
    __table_args__ = (UniqueConstraint("hero_id", "kind", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(64))
    qty: Mapped[int] = mapped_column(Integer, default=1)


class DisuseTalk(Timestamps, Base):
    """The scene a hero is in the middle of: at most one each. ``text`` is the dialog the game's ``Disuse.scene`` wrote for it (kept, since
    ``pos`` is a place in it), ``prompt`` what the hero was last asked, and ``tree`` and ``stack`` where an included tree stands, as in
    ``npcs.models.NpcTalk``."""

    __tablename__ = "disuse_talks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    pos: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tree: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stack: Mapped[list] = mapped_column(JSON, default=list)
