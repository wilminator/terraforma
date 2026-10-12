"""The tables of the sign-in provider: the subject ids players get, the one-use codes and the access tokens.

Codes and tokens are random values the engine hands out; only their SHA-256 is stored, so a copy of the database holds nothing
that can be replayed. Times are whole seconds on the wall clock (``wallclock.timestamp``).
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class OidcSubject(Base):
    """The opaque, stable id one relying site knows a player by. Random per account and site, so two sites cannot match players."""

    __tablename__ = "oidc_subjects"
    __table_args__ = (UniqueConstraint("subject"),)

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(43))


class OidcCode(Base):
    """An authorization code a site has been sent and has not yet traded for tokens. Works once, for a minute."""

    __tablename__ = "oidc_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    # The PKCE challenge (S256) the code is tied to.
    challenge: Mapped[str] = mapped_column(String(43))
    nonce: Mapped[str | None] = mapped_column(String(256))
    scope: Mapped[str] = mapped_column(String(64))
    # The account's session version when the code was made: a password reset (which bumps it) voids the code.
    session_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    # Kept until it expires, so that a code shown a second time is known for a replay and the tokens it made are revoked.
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class OidcToken(Base):
    """An access token for the userinfo call."""

    __tablename__ = "oidc_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    scope: Mapped[str] = mapped_column(String(64))
    session_version: Mapped[int] = mapped_column(Integer)
    # The code it was traded for, to revoke it if that code is ever shown again.
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
