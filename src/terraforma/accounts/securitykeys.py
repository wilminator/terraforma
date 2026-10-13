"""Security keys and passkeys (WebAuthn, FIDO2) as a second factor, next to the authenticator app.

The protocol is the ``webauthn`` library's (CBOR, COSE keys and signatures are not written here). What this module decides:

* The relying party is this site: its id is the host of ``Settings.public_url`` and the only origin that counts is that
  address, so a key made for one game never answers for another.
* No attestation is asked for: the engine wants to know the player holds a key, not which make it is.
* A key may be a passkey (the authenticator may keep it as a discoverable credential, so a phone or a password manager can
  hold it), and the player is asked to verify themselves on it when it can.
* A challenge is made for one use and kept in the signed session cookie; it is taken out when read, so it works once.
* A key counts as a second factor only once the emailed link has confirmed it, like every change to two-factor login.
"""

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..models import Account, WebauthnKey

#: How long the browser is given to answer, in milliseconds (a key can need a tap, a fingerprint and a PIN).
TIMEOUT_MS = 120_000
NAME_MAX = 40
DEFAULT_NAME = "Security key"


class SecurityKeyError(ValueError):
    """A registration or answer that does not check out; the message is safe to show."""


@dataclass(frozen=True)
class RelyingParty:
    id: str
    origin: str


def relying_party(public_url: str) -> RelyingParty:
    """The site as WebAuthn knows it: the host (no port) and the origin the browser reports."""
    parts = urlsplit(public_url)
    if not parts.hostname:
        raise ValueError("public_url has no host")
    return RelyingParty(id=parts.hostname, origin=f"{parts.scheme}://{parts.netloc}")


def clean_name(name: str | None) -> str:
    name = " ".join((name or "").split())[:NAME_MAX]
    return name or DEFAULT_NAME


def _descriptors(keys: list[WebauthnKey]) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id)) for key in keys]


def registration_options(party: RelyingParty, game_name: str, account: Account, keys: list[WebauthnKey]) -> tuple[str, bytes]:
    """The options for ``navigator.credentials.create`` (JSON text) and the challenge to remember. Keys the account has
    already are excluded, so the same key is not registered twice."""
    options = generate_registration_options(
        rp_id=party.id,
        rp_name=game_name,
        user_id=account.id.to_bytes(8, "big"),
        user_name=account.username,
        user_display_name=account.username,
        timeout=TIMEOUT_MS,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.PREFERRED
        ),
        exclude_credentials=_descriptors(keys),
    )
    return options_to_json(options), options.challenge


@dataclass(frozen=True)
class NewKey:
    credential_id: str
    public_key: str
    sign_count: int


def verify_registration(party: RelyingParty, credential: str, challenge: bytes) -> NewKey:
    """Checks what ``navigator.credentials.create`` returned (as JSON text) against the challenge made for it."""
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=party.id, expected_origin=party.origin
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as error:
        raise SecurityKeyError("the security key could not be added") from error
    return NewKey(bytes_to_base64url(verified.credential_id), bytes_to_base64url(verified.credential_public_key), verified.sign_count)


def authentication_options(party: RelyingParty, keys: list[WebauthnKey]) -> tuple[str, bytes]:
    """The options for ``navigator.credentials.get`` (JSON text), allowing the account's keys, and the challenge to remember."""
    options = generate_authentication_options(
        rp_id=party.id,
        timeout=TIMEOUT_MS,
        allow_credentials=_descriptors(keys),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options), options.challenge


def credential_id_of(credential: str) -> str | None:
    """The key an answer says it is from, or None when the answer is not shaped right."""
    try:
        found = json.loads(credential)["id"]
    except (ValueError, KeyError, TypeError):
        return None
    return found if isinstance(found, str) and 0 < len(found) <= 255 else None


def verify_authentication(party: RelyingParty, credential: str, challenge: bytes, key: WebauthnKey) -> int:
    """Checks an answer to ``navigator.credentials.get`` against the challenge and $key. Returns the key's new counter."""
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=party.id,
            expected_origin=party.origin,
            credential_public_key=base64url_to_bytes(key.public_key),
            credential_current_sign_count=key.sign_count,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as error:
        raise SecurityKeyError("that security key was not accepted") from error
    return verified.new_sign_count


def describe(key: WebauthnKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "added": key.confirmed_at.isoformat() if key.confirmed_at else None,
        "last_used": key.last_used_at.isoformat() if key.last_used_at else None,
    }
