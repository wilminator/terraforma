"""Who is asking: the login in the session cookie, and the CSRF token.

Logging in puts the account id, its session version and a fresh CSRF
token in the signed session cookie, and hands the token to the page.
Every call that changes something sends it back in the X-CSRF-Token
header; another site can make the browser send the cookie, but it can't
read the token. A login only counts while its session version matches the
account's: bumping that (a password reset does) ends every login at once.

WebSockets carry no such header, so the fight socket checks the Origin
instead: only pages from this site may open one.
"""

import hmac
import secrets

from fastapi import Request, WebSocket
from itsdangerous import SignatureExpired, TimestampSigner
from starlette.middleware.sessions import SessionMiddleware

# How far ahead of the server's clock a signature may be dated and still count. A clock that
# steps back (a virtual machine's, or one being corrected) makes a login made a moment ago look
# like it is from the future, and itsdangerous refuses that: the player would be logged out for
# no reason. A few seconds of leeway costs nothing, since a signature this young is not old.
CLOCK_LEEWAY = 10


class _LenientSigner(TimestampSigner):
    def unsign(self, signed_value, max_age=None, return_timestamp=False):
        try:
            return super().unsign(signed_value, max_age=max_age, return_timestamp=return_timestamp)
        except SignatureExpired as error:
            signed_at = error.date_signed
            if signed_at is None or int(signed_at.timestamp()) - self.get_timestamp() > CLOCK_LEEWAY:
                raise
            # Dated slightly ahead of this clock: the signature itself was checked, so accept it.
            return super().unsign(signed_value, max_age=None, return_timestamp=return_timestamp)


class TolerantSessionMiddleware(SessionMiddleware):
    """The session cookie middleware, forgiving a clock that stepped back by a few seconds (CLOCK_LEEWAY)."""

    def __init__(self, app, secret_key, *args, **kwargs):
        super().__init__(app, secret_key, *args, **kwargs)
        self.signer = _LenientSigner(str(secret_key))

SESSION_ACCOUNT = "account_id"
SESSION_VERSION = "version"
SESSION_CSRF = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def start_session(request: Request, account_id: int, version: int) -> str:
    request.session.clear()
    request.session[SESSION_ACCOUNT] = account_id
    request.session[SESSION_VERSION] = version
    token = secrets.token_urlsafe(32)
    request.session[SESSION_CSRF] = token
    return token


def csrf_matches(request: Request) -> bool:
    expected = request.session.get(SESSION_CSRF, "")
    sent = request.headers.get(CSRF_HEADER, "")
    return bool(expected) and hmac.compare_digest(expected, sent)


def same_origin(socket: WebSocket) -> bool:
    origin = socket.headers.get("origin")
    if origin is None:
        return False
    host = socket.headers.get("host", "")
    return origin.split("://", 1)[-1] == host
