"""A player's play time."""

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class PlayTime(Base):
    """The seconds an account has played, as of ``counted_to``, the wall-clock second (UNIX time) up to which time has been
    counted: two stretches that overlap count once. ``last_input_at`` is the second of the account's latest input call, whose
    next few seconds are credited too but not yet settled (see ``playtime.service``)."""

    __tablename__ = "play_times"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    seconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    counted_to: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_input_at: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
