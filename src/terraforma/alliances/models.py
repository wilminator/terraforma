"""Alliances of teams: who is in one and in what role, and who has been asked."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps, utcnow

NAME_MAX = 32
ROLE_MAX = 16


class Alliance(Timestamps, Base):
    __tablename__ = "alliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kept as typed; unique ignoring case and spacing.
    name: Mapped[str] = mapped_column(String(NAME_MAX))
    name_key: Mapped[str] = mapped_column(String(NAME_MAX), unique=True)
    #: The treasury: gold the member teams have given and the alliance has not spent (``alliances.treasury``).
    gold: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    #: Whole percents of the gold its member teams earn that the alliance keeps: from fighting, and from selling on a market
    #: (``alliances.taxes``). Nothing until the alliance sets a rate.
    fight_tax: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sales_tax: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AllianceMember(Base):
    """A team in an alliance, in a role (one of the game's ``Alliances.roles``). ``id`` runs in the order teams joined."""

    __tablename__ = "alliance_members"
    __table_args__ = (UniqueConstraint("alliance_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    role: Mapped[str] = mapped_column(String(ROLE_MAX))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Set for a subcommander the members voted in: until then (wall clock, seconds) no one but a vote of the members removes the role.
    protected_until: Mapped[int | None] = mapped_column(BigInteger)


class AllianceAlumnus(Base):
    """A team that was in the alliance and left or was removed. If the alliance dissolves it still has a share of the treasury."""

    __tablename__ = "alliance_alumni"
    __table_args__ = (UniqueConstraint("alliance_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)


class AllianceRole(Base):
    """A role of tier 3 an alliance has defined (see ``hooks``): a name and the actions it may do. The commander and
    subcommanders are not here; their powers are the tiers'."""

    __tablename__ = "alliance_roles"
    __table_args__ = (UniqueConstraint("alliance_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    name: Mapped[str] = mapped_column(String(ROLE_MAX))
    actions: Mapped[list] = mapped_column(JSON)


class AllianceRule(Base):
    """A voting requirement: ``action`` done by a team in ``role`` (empty: any role without a rule of its own) needs a ballot that
    ``percent`` of the alliance's active teams vote yes in. No rule, no vote needed."""

    __tablename__ = "alliance_rules"
    __table_args__ = (UniqueConstraint("alliance_id", "action", "role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    action: Mapped[str] = mapped_column(String(24))
    role: Mapped[str] = mapped_column(String(ROLE_MAX), default="")
    percent: Mapped[int] = mapped_column(Integer)


class AllianceInvite(Base):
    """An alliance's invitation to a team, until the team accepts or declines it or the alliance withdraws it."""

    __tablename__ = "alliance_invites"
    __table_args__ = (UniqueConstraint("alliance_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    invited_by_team_id: Mapped[int] = mapped_column(Integer)


class Ballot(Base):
    """A question an alliance puts to its teams. ``kind`` and ``payload`` are the game's, for acting on the result
    (``Alliances.on_ballot_closed``); the engine only counts. Times are seconds (the wall clock: players are waiting)."""

    __tablename__ = "alliance_ballots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(120))
    options: Mapped[list] = mapped_column(JSON)
    payload: Mapped[dict | None] = mapped_column(JSON)
    #: A secret ballot keeps who voted (``BallotVoter``) apart from what was voted (``BallotVote``, with no team on it).
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_by_team_id: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[int] = mapped_column(BigInteger)
    closes_at: Mapped[int | None] = mapped_column(BigInteger)
    closed_at: Mapped[int | None] = mapped_column(BigInteger)
    #: A ballot that carries an action (``kind`` ``"action"``; the ``payload`` says which) passes when this percent of the
    #: active teams who may vote vote for option 0 (yes). None on a plain ballot.
    threshold: Mapped[int | None] = mapped_column(Integer)
    #: Only teams in these roles may vote (None: every team whose role may).
    electorate: Mapped[list | None] = mapped_column(JSON)
    #: Set when it closes: ``{"winner": index or None, "totals": [...], "turnout": n, "eligible": n}``.
    result: Mapped[dict | None] = mapped_column(JSON)


class BallotVoter(Base):
    """That a team has voted in a ballot (and when), never for what."""

    __tablename__ = "alliance_ballot_voters"
    __table_args__ = (UniqueConstraint("ballot_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ballot_id: Mapped[int] = mapped_column(ForeignKey("alliance_ballots.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)


class BallotVote(Base):
    """A vote: for the option at ``option``, ``weight`` strong. ``team_id`` is set on a public ballot (and may change until it closes) and left empty on a secret one."""

    __tablename__ = "alliance_ballot_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ballot_id: Mapped[int] = mapped_column(ForeignKey("alliance_ballots.id"), index=True)
    team_id: Mapped[int | None] = mapped_column(Integer)
    option: Mapped[int] = mapped_column(Integer)
    weight: Mapped[int] = mapped_column(Integer, default=1)
