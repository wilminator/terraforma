"""A player's Challenge Token balance and the ledger behind it."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class ChallengeBalance(Timestamps, Base):
    """What an account holds: one balance per account. Always the sum of its ledger rows (``challenge.service.audit`` checks it)."""

    __tablename__ = "challenge_balances"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class ChallengeEntry(Timestamps, Base):
    """One change to a balance: who, how many (negative for a spend), why, and where it came from.

    ``reason`` is ``fight`` (earned: counts toward the daily cap), ``purchase`` (credited through the server-to-server
    call) or whatever a game passes to ``change``. A fight pays an account once: the (fight, account) pair is unique (rows
    with no fight are not limited). A purchase carries the buyer's idempotency key, unique, so the same
    purchase is never credited twice (rows with no key are not limited)."""

    __tablename__ = "challenge_ledger"
    __table_args__ = (UniqueConstraint("fight_id", "account_id"), UniqueConstraint("idempotency_key"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(64))
    fight_id: Mapped[int | None] = mapped_column(ForeignKey("fights.id"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
