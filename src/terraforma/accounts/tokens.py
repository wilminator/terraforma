"""Signed, expiring tokens for links sent by email.

A token says what it's for (its purpose), which account, and when it was
made, signed with the server's secret so it can't be forged or reused for
another purpose. Each purpose has a lifetime. A token also carries
whatever must still be true for it to count: the email address being
confirmed, or the account's session version for a password reset (which
the reset itself bumps, so each reset link works once), or the nonce
of the 2FA change it confirms.
"""

from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from .. import wallclock

LIFETIMES = {
    "confirm-email": 3 * 24 * 60 * 60,
    "password-reset": 60 * 60,
    "twofa-change": 60 * 60,
}


class TokenError(ValueError):
    """A token that's forged, for something else, or too old."""


@dataclass(frozen=True)
class Token:
    account_id: int
    data: dict[str, Any]


class Tokens:
    def __init__(self, secret: str):
        self._secret = secret

    def _serializer(self, purpose: str) -> URLSafeSerializer:
        if purpose not in LIFETIMES:
            raise ValueError(f"unknown token purpose {purpose}")
        return URLSafeSerializer(self._secret, salt=f"terraforma:{purpose}")

    def make(self, purpose: str, account_id: int, **data: Any) -> str:
        return self._serializer(purpose).dumps({"a": account_id, "t": wallclock.timestamp(), "d": data})

    def read(self, purpose: str, token: str) -> Token:
        try:
            payload = self._serializer(purpose).loads(token)
        except BadSignature as error:
            raise TokenError("not a valid link") from error
        age = wallclock.timestamp() - int(payload["t"])
        if age < 0 or age > LIFETIMES[purpose]:
            raise TokenError("this link has expired")
        return Token(account_id=int(payload["a"]), data=dict(payload["d"]))
