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
