"""Which fights a dialog started for a team, under the dialog's own name for the fight, so a later dialog can ask how it ended."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class ScriptedFight(Timestamps, Base):
    """A fight a dialog's ``npc_fight`` tag started: one row for each team of the party that fought, under the dialog's $key for it. A team's
    latest fight under a key is the one ``npc_fight_result`` reads. The rows go with the fight when it is swept (``fighttags.service``) and
    with the team when it is deleted."""

    __tablename__ = "scripted_fights"
    __table_args__ = (UniqueConstraint("fight_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
