"""A player's token balance and the ledger behind it."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class TokenBalance(Timestamps, Base):
    """What an account holds. Always the sum of its ledger rows (``tokens.service.audit`` checks it)."""

    __tablename__ = "token_balances"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class TokenEntry(Timestamps, Base):
    """One change to a balance: who, how many (negative for a spend), why, and the fight it came from, if any.
    A fight pays an account once: the (fight, account) pair is unique (rows with no fight are not limited)."""

    __tablename__ = "token_ledger"
    __table_args__ = (UniqueConstraint("fight_id", "account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(64))
    fight_id: Mapped[int | None] = mapped_column(ForeignKey("fights.id"), nullable=True)
