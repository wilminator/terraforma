"""Contracts between two teams. A contract is an offer from side ``a`` to side ``b`` at a guild; each side has terms it is bound to and
a breach escrow it puts down."""

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

OFFERED = "offered"
ACTIVE = "active"
FULFILLED = "fulfilled"
BREACHED = "breached"
DECLINED = "declined"
WITHDRAWN = "withdrawn"
EXPIRED = "expired"

PENDING = "pending"
MET = "met"
BROKEN = "broken"

SIDES = ("a", "b")


class Contract(Timestamps, Base):
    """One contract. Side ``a`` (``a_team_id``) made the offer and side ``b`` was asked. ``state`` goes from ``offered`` to ``active``
    (``b`` accepted; it runs ``duration`` seconds from ``starts_at``) or ``declined``, ``withdrawn`` (``a`` took it back) or ``expired``
    (``b`` did not answer by ``offer_expires_at``); an active one ends ``fulfilled`` (every term met, escrows returned) or ``breached``
    (``breached_by`` names the side(s) that broke a term, whose escrow went to the other side). Escrow amounts are gold; the deposits
    themselves are ``a_escrow_id`` and ``b_escrow_id``.

    An *open* contract is posted by the guild itself (``a_team_id`` is none): it binds only side ``b``, the first team to take it, and pays
    that team ``reward_gold`` when every term is met. A breach loses their escrow to the guild.

    ``payment_gold`` is a fee side ``a`` pays side ``b`` for keeping b's terms. It is put into a separate deposit when the offer is made
    (so it is returned if the offer ends unanswered) and is paid to ``b`` when the contract ends with none of b's terms broken, and
    returned to ``a`` otherwise. It is separate from the breach escrows."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild: Mapped[str] = mapped_column(String(64), index=True)
    a_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)  # none: the guild posted it (open)
    b_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)  # none: nobody has taken it yet
    reward_gold: Mapped[int] = mapped_column(BigInteger, default=0)  # an open contract: what the guild pays the team that keeps it
    a_escrow_gold: Mapped[int] = mapped_column(BigInteger, default=0)
    b_escrow_gold: Mapped[int] = mapped_column(BigInteger, default=0)
    a_escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True)
    b_escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True)
    payment_gold: Mapped[int] = mapped_column(BigInteger, default=0)  # side a's fee to side b, held from the offer, paid when b keeps every term
    payment_escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True)
    duration: Mapped[int] = mapped_column(BigInteger)
    offer_expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    starts_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ends_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(10), default=OFFERED, index=True)
    breached_by: Mapped[str | None] = mapped_column(String(2), nullable=True)  # "a", "b" or "ab"
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # wall clock, whole seconds


class ContractTerm(Base):
    """One thing a side is bound to: a ``kind`` the game's ``Contracts.kinds`` knows and its ``params``. ``state`` goes from ``pending`` to
    ``met`` or ``broken`` and never changes again."""

    __tablename__ = "contract_terms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), index=True)
    side: Mapped[str] = mapped_column(String(1))
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(8), default=PENDING)


BOUNTY_OPEN = "open"
BOUNTY_PAID = "paid"
BOUNTY_EXPIRED = "expired"
BOUNTY_WITHDRAWN = "withdrawn"


class Bounty(Timestamps, Base):
    """A reward for killing a team (or one hero of it), posted by a team or in an alliance's name, that anyone may earn without a contract.
    ``reward_gold`` is held from the posting (in the team's deposit ``escrow_id``, or taken from the alliance's treasury) and paid once, to
    the teams that fought the target's fall, when the target has fallen after the posting; it goes back to the poster if nobody does before
    ``expires_at`` or the poster withdraws it. ``poster_team_id`` is the team that posted it, also for an alliance's bounty (``alliance_id``;
    the gold goes back to that team if the alliance is gone by then)."""

    __tablename__ = "bounties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild: Mapped[str] = mapped_column(String(64), index=True)
    poster_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    alliance_id: Mapped[int | None] = mapped_column(ForeignKey("alliances.id", ondelete="SET NULL"), nullable=True)
    target_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    scope: Mapped[str] = mapped_column(String(4))  # "team" or "hero"
    hero_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reward_gold: Mapped[int] = mapped_column(BigInteger)
    escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True)
    posted_at: Mapped[int] = mapped_column(BigInteger)  # wall clock, whole seconds: falls before it do not count
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    state: Mapped[str] = mapped_column(String(10), default=BOUNTY_OPEN, index=True)
    paid_to: Mapped[list | None] = mapped_column(JSON, nullable=True)  # the team ids that were paid
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
