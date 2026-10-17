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
    that team ``reward_gold`` when every term is met. A breach loses their escrow to the guild."""

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
