"""Two-factor login: time-based codes (TOTP, RFC 6238) from an authenticator app, and recovery codes.

Setup stores a fresh secret (encrypted, see keys.encrypted_column) that
doesn't count until the player proves the app works and then follows an
emailed link: every change to 2FA (turn on, turn off, new recovery codes)
is confirmed that way, with a token that works once and is replaced by any
newer request (twofa_change_nonce).

A code works once: the step it was accepted for is remembered, and that
step or an earlier one is refused afterwards. A recovery code is a long
random code, kept only as a hash, that works once instead of an app code.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

from .. import wallclock
from ..keys import KeyRing
from ..models import Account
from .service import AccountError

PURPOSE = "accounts.totp_secret"
STEP_SECONDS = 30
DIGITS = 6
# A phone clock a little off still works: the step before and after count too.
DRIFT_STEPS = 1
RECOVERY_CODES = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{number:0{DIGITS}d}"


def current_step() -> int:
    return wallclock.timestamp() // STEP_SECONDS


def matching_step(secret: str, code: str, after: int | None) -> int | None:
    """The step $code is right for, if it's a recent one not used before; else None."""
    if len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    now = current_step()
    found = None
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        # Every step is compared, so the time taken doesn't say which was close.
        if hmac.compare_digest(code_at(secret, step), code) and (after is None or step > after):
            found = step
    return found


def uri(secret: str, username: str, issuer: str) -> str:
    """What the authenticator app's QR code holds."""
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}"


def enabled(account: Account) -> bool:
    return account.totp_enabled_at is not None


def start_setup(ring: KeyRing, account: Account) -> str:
    """A new secret, kept (encrypted) but not yet counting. Replaces an unfinished setup, never a live one."""
    if enabled(account):
        raise AccountError("two-factor login is already on: turn it off first")
    secret = new_secret()
    account.totp_secret = ring.encrypt(secret, PURPOSE)
    account.totp_last_step = None
    return secret


def _hash(code: str) -> str:
    return hashlib.sha256(_normal(code).encode()).hexdigest()


def _normal(code: str) -> str:
    return code.replace("-", "").replace(" ", "").casefold()


def make_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        text = base64.b32encode(secrets.token_bytes(10)).decode().casefold()
        codes.append("-".join(text[index : index + 4] for index in range(0, 16, 4)))
    return codes


def set_recovery_codes(account: Account) -> list[str]:
    """New recovery codes, replacing any; the plain codes are returned this once."""
    codes = make_recovery_codes()
    account.recovery_codes = [_hash(code) for code in codes]
    return codes


def check_code(ring: KeyRing, account: Account, code: str) -> bool:
    """Whether $code is a live app code (when setting up or on) or an unused recovery code, and uses it up."""
    if account.totp_secret is None:
        return False
    code = code.strip()
    secret = ring.decrypt(account.totp_secret, PURPOSE)
    step = matching_step(secret, _normal(code), account.totp_last_step)
    if step is not None:
        account.totp_last_step = step
        return True
    if enabled(account):
        digest = _hash(code)
        remaining = list(account.recovery_codes or [])
        for stored in remaining:
            if hmac.compare_digest(stored, digest):
                # A new list, so the JSON column notices the change.
                account.recovery_codes = [other for other in remaining if other != stored]
                return True
    return False


def open_change(account: Account) -> str:
    """Starts an emailed change: its nonce goes in the link, and any earlier link stops working."""
    account.twofa_change_nonce = secrets.token_urlsafe(32)
    return account.twofa_change_nonce


def close_change(account: Account, nonce: str) -> bool:
    """Uses up the change the link was made for. False if it was replaced or already used."""
    current = account.twofa_change_nonce
    if current is None or not secrets.compare_digest(current, nonce):
        return False
    account.twofa_change_nonce = None
    return True
