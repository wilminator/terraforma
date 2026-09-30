"""Who is asking: the login in the session cookie, and the CSRF token.

Logging in puts the account id and a fresh CSRF token in the signed
session cookie, and hands the token to the page. Every call that changes
something sends it back in the X-CSRF-Token header; another site can
make the browser send the cookie, but it can't read the token.

WebSockets carry no such header, so the fight socket checks the Origin
instead: only pages from this site may open one.
"""

import hmac
import secrets

from fastapi import HTTPException, Request, WebSocket, status

SESSION_ACCOUNT = "account_id"
SESSION_CSRF = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def start_session(request: Request, account_id: int) -> str:
    request.session.clear()
    request.session[SESSION_ACCOUNT] = account_id
    token = secrets.token_urlsafe(32)
    request.session[SESSION_CSRF] = token
    return token


def current_account_id(request: Request) -> int:
    """For calls that only read: the logged-in account, or 401."""
    account_id = request.session.get(SESSION_ACCOUNT)
    if account_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    return account_id


def acting_account_id(request: Request) -> int:
    """For calls that change something: logged in, and the CSRF token matches."""
    account_id = current_account_id(request)
    expected = request.session.get(SESSION_CSRF, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not expected or not hmac.compare_digest(expected, sent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or wrong CSRF token")
    return account_id


def same_origin(socket: WebSocket) -> bool:
    origin = socket.headers.get("origin")
    if origin is None:
        return False
    host = socket.headers.get("host", "")
    return origin.split("://", 1)[-1] == host
