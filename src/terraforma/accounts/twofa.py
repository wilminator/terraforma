"""Two-factor login: time-based codes (TOTP, RFC 6238) from an authenticator app, security keys (accounts.securitykeys), and
recovery codes. Two-factor login is on when at least one method is: the app, or a key.

Setup stores a fresh secret (encrypted, see keys.encrypted_column) that
doesn't count until the player proves the app works and then follows an
emailed link: every change to 2FA (turn on, turn off, new recovery codes)
is confirmed that way, with a token that works once and is replaced by any
newer request (twofa_change_nonce).

A code works once: the step it was accepted for is remembered, and that
step or an earlier one is refused afterwards. A recovery code is a long
random code, kept only as a hash, that works once instead of an app code or a
key. The recovery codes are made with the first method and go with the last.
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


def app_on(account: Account) -> bool:
    """Whether the authenticator app counts (its setup was confirmed)."""
    return account.totp_enabled_at is not None


def enabled(account: Account) -> bool:
    """Whether two-factor login is on: the app counts, or at least one security key does."""
    return app_on(account) or account.webauthn_count > 0


def methods(account: Account) -> list[str]:
    """The methods that count: "app" and/or "key" (recovery codes are not a method of their own)."""
    return (["app"] if app_on(account) else []) + (["key"] if account.webauthn_count > 0 else [])


def start_setup(ring: KeyRing, account: Account) -> str:
    """A new secret, kept (encrypted) but not yet counting. Replaces an unfinished setup, never a live one."""
    if app_on(account):
        raise AccountError("the authenticator app is already set up: remove it first")
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


def first_recovery_codes(account: Account) -> list[str] | None:
    """The recovery codes for a method that has just been turned on: new ones when it is the first (returned this once), else
    None, because the codes the account has still stand."""
    return set_recovery_codes(account) if account.recovery_codes is None else None


def clear_app(account: Account) -> None:
    """Takes the authenticator app away (and, if it was the last method, the recovery codes with it)."""
    account.totp_secret = account.totp_enabled_at = account.totp_last_step = None
    if not enabled(account):
        account.recovery_codes = None


def check_code(ring: KeyRing, account: Account, code: str, *, setting_up: bool = False) -> bool:
    """Whether $code is a live app code or an unused recovery code, and uses it up. An app code counts once the app is on, or
    while it is being set up ($setting_up: only the call that turns it on says so); a recovery code counts while two-factor
    login is on."""
    code = code.strip()
    if account.totp_secret is not None and (app_on(account) or setting_up):
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


def check_setup_code(ring: KeyRing, account: Account, code: str) -> bool:
    """Whether $code is what the app shows for the secret being set up (never a recovery code), and uses it up."""
    if account.totp_secret is None or app_on(account):
        return False
    step = matching_step(ring.decrypt(account.totp_secret, PURPOSE), _normal(code.strip()), account.totp_last_step)
    if step is None:
        return False
    account.totp_last_step = step
    return True


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
