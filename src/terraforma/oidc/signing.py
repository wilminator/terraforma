"""The keys that sign ID tokens, made from the engine's two encryption key slots.

Nothing extra is stored or backed up: the signing key of a slot is derived (HKDF-SHA256, with a purpose of its own) from that
slot's encryption key, so ``keys rotate`` rotates the signing keys too. The current slot signs; both slots are published in the
key set, so a token signed just before a rotation still checks out until the next one. The curve is P-256 (ES256).
"""

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..keys import KeyRing

ALGORITHM = "ES256"
_PURPOSE = b"terraforma:oidc:id-token-signing:es256"
# The order of the P-256 group: a private key is a number from 1 to ORDER - 1.
_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class SigningKey:
    private: ec.EllipticCurvePrivateKey
    kid: str
    jwk: dict


def _derive(secret: bytes) -> SigningKey:
    # 48 bytes reduced into the group, so every number is as likely as any other.
    material = HKDF(algorithm=hashes.SHA256(), length=48, salt=None, info=_PURPOSE).derive(secret)
    private = ec.derive_private_key(int.from_bytes(material, "big") % (_ORDER - 1) + 1, ec.SECP256R1())
    point = private.public_key().public_numbers()
    members = {"crv": "P-256", "kty": "EC", "x": _b64(point.x.to_bytes(32, "big")), "y": _b64(point.y.to_bytes(32, "big"))}
    # The key's name is its RFC 7638 thumbprint, so it is the same on every server that holds the key.
    kid = _b64(hashlib.sha256(json.dumps(members, sort_keys=True, separators=(",", ":")).encode()).digest())
    return SigningKey(private, kid, {**members, "use": "sig", "alg": ALGORITHM, "kid": kid})


_derive_cached = lru_cache(maxsize=8)(_derive)


def signing_keys(ring: KeyRing) -> list[SigningKey]:
    """The current slot's signing key first, then the previous slot's, if there is one."""
    slots = [ring.current] + ([ring.previous] if ring.previous is not None else [])
    return [_derive_cached(slot.secret) for slot in slots]


def key_set(ring: KeyRing) -> dict:
    """The public keys as a JSON Web Key Set, for ``jwks_uri``."""
    return {"keys": [key.jwk for key in signing_keys(ring)]}


def sign(ring: KeyRing, claims: dict) -> str:
    """The claims as an ES256 token signed with the current key."""
    key = signing_keys(ring)[0]
    return jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid})
