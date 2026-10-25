"""A quest the engine made for a gap in the world's progression, before and after it goes live."""

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: Why a quest was made: no quest of the band's slot fits the band, or none of the band's teams completed a quest for the stall time.
NO_FIT, STALLED = "no_fit", "stalled"
#: A live settlement's supply of a material fell below the game's floor (``Needs.supply_floor``).
SUPPLY = "supply"
#: Where a proposal stands: waiting for an admin, live (a template of the band's slot), turned down, or retired (taken out of the slot).
PROPOSED, LIVE, REJECTED, RETIRED = "proposed", "live", "rejected", "retired"


class QuestProposal(Timestamps, Base):
    """A quest the engine made up for a band of teams out of existing monsters promoted to bosses. Whether it waits for an admin or goes live
    at once is the game's choice (``Needs.needs_approval``); live, it is a template of the band's rotating slot, so the slot offers it from its next
    period. ``spec`` is ``{"bosses": [...], "band": key, "reason": ...}`` (see ``quests.bosses``). A band has one open proposal (proposed or live) at
    a time, so a gap is not answered twice."""

    __tablename__ = "quest_proposals"
    # ``open`` is 1 while the proposal is proposed or live and NULL after, so the database allows one open proposal per band (NULLs never clash).
    __table_args__ = (UniqueConstraint("band", "open"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    band: Mapped[str] = mapped_column(String(64), index=True)
    slot: Mapped[str] = mapped_column(String(64))
    pxp_min: Mapped[int] = mapped_column(Integer, default=0)
    pxp_max: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=PROPOSED)
    open: Mapped[int | None] = mapped_column(Integer, default=1)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("quest_templates.id"))
