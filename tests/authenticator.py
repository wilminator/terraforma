"""A software security key for the tests: makes the answers a browser would send for ``navigator.credentials.create`` and
``navigator.credentials.get`` (WebAuthn level 2, ES256, no attestation), signed with a key of its own."""

import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

FLAG_PRESENT, FLAG_VERIFIED, FLAG_ATTESTED = 0x01, 0x04, 0x40


class SoftKey:
    def __init__(self, origin: str = "http://game.test", rp_id: str = "game.test", counter: int = 1):
        self.origin, self.rp_id, self.counter = origin, rp_id, counter
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)

    @property
    def id(self) -> str:
        return bytes_to_base64url(self.credential_id)

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}).encode()

    def _cose_key(self) -> bytes:
        numbers = self.private.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    def _auth_data(self, *, attested: bool, rp_id: str | None = None) -> bytes:
        flags = FLAG_PRESENT | FLAG_VERIFIED | (FLAG_ATTESTED if attested else 0)
        data = hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([flags]) + struct.pack(">I", self.counter)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + self._cose_key()
        return data

    def create(self, options: dict, *, origin: str | None = None, challenge: str | None = None) -> str:
        """What the browser sends after ``navigator.credentials.create(options)``, as JSON text."""
        client = self._client_data("webauthn.create", challenge or options["challenge"], origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(attested=True)})
        return json.dumps(
            {
                "id": self.id,
                "rawId": self.id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": bytes_to_base64url(client),
                    "attestationObject": bytes_to_base64url(attestation),
                    "transports": ["usb"],
                },
                "clientExtensionResults": {},
                "authenticatorAttachment": "cross-platform",
            }
        )

    def get(self, options: dict, *, origin: str | None = None, rp_id: str | None = None, challenge: str | None = None, bump: bool = True) -> str:
        """What the browser sends after ``navigator.credentials.get(options)``, as JSON text."""
        if bump:
            self.counter += 1
        client = self._client_data("webauthn.get", challenge or options["challenge"], origin)
        data = self._auth_data(attested=False, rp_id=rp_id)
        signature = self.private.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return json.dumps(
            {
                "id": self.id,
                "rawId": self.id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": bytes_to_base64url(client),
                    "authenticatorData": bytes_to_base64url(data),
                    "signature": bytes_to_base64url(signature),
                    "userHandle": None,
                },
                "clientExtensionResults": {},
                "authenticatorAttachment": "cross-platform",
            }
        )


def challenge_of(options: dict) -> bytes:
    return base64url_to_bytes(options["challenge"])
