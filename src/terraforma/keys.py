"""Encryption keys, in two slots: current and previous.

Secrets the engine must be able to read back (a 2FA secret, say) are stored
encrypted (AES-256-GCM). New values are always encrypted with the current
key. A value made with the previous key still reads, so rotating the key
never locks anyone out:

    python -m terraforma keys new       make the first key (refuses if there is one)
    python -m terraforma keys rotate    current becomes previous, a new current is made,
                                        and every stored value is re-encrypted with it

The keys are files in ``key_dir`` (settings.toml), ``current.key`` and
``previous.key``, readable only by the server. Back them up apart from the
database: a database backup without its keys can't be read.

A stored value says which key made it and what it is for (its purpose,
such as "accounts.totp_secret"), so a value copied into another column
doesn't decrypt there.
"""

import base64
import hashlib
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Column, Table, select, update
from sqlalchemy.ext.asyncio import AsyncSession

SLOTS = ("current", "previous")
PREFIX = "tf1."
KEY_BYTES = 32
_ID_BYTES = 8
_NONCE_BYTES = 12


class KeysMissing(RuntimeError):
    """No current key: the server can't start without one."""


class KeyFileError(RuntimeError):
    """A key file that isn't a key."""


class DecryptError(ValueError):
    """A stored value that no key in either slot made, or that was changed or moved."""


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Key:
    secret: bytes

    @property
    def id(self) -> bytes:
        """Names the key inside each value it encrypts, without revealing it."""
        return hashlib.sha256(b"terraforma key id:" + self.secret).digest()[:_ID_BYTES]


def key_path(key_dir: Path, slot: str) -> Path:
    return Path(key_dir) / f"{slot}.key"


def _read_key(path: Path) -> Key | None:
    if not path.exists():
        return None
    try:
        secret = _decode(path.read_text().strip())
    except ValueError as error:
        raise KeyFileError(f"{path} is not a key file") from error
    if len(secret) != KEY_BYTES:
        raise KeyFileError(f"{path} is not a key file")
    return Key(secret)


def _write_key(path: Path, key: Key) -> None:
    """Writes the key readable by the server's user only, replacing any file atomically."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(_encode(key.secret) + "\n")
    os.replace(temporary, path)


class KeyRing:
    def __init__(self, current: Key, previous: Key | None = None):
        self.current = current
        self.previous = previous

    @classmethod
    def load(cls, key_dir: Path) -> "KeyRing":
        current = _read_key(key_path(key_dir, "current"))
        if current is None:
            raise KeysMissing(f"no encryption key in {key_dir}: make one with `python -m terraforma keys new`")
        return cls(current, _read_key(key_path(key_dir, "previous")))

    def _key_for(self, key_id: bytes) -> Key:
        for key in (self.current, self.previous):
            if key is not None and secrets.compare_digest(key.id, key_id):
                return key
        raise DecryptError("made with a key that is in neither slot")

    def encrypt(self, plaintext: str, purpose: str) -> str:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = AESGCM(self.current.secret).encrypt(nonce, plaintext.encode(), _associated(purpose))
        return PREFIX + _encode(self.current.id + nonce + sealed)

    def decrypt(self, stored: str, purpose: str) -> str:
        raw = _unwrap(stored)
        key = self._key_for(raw[:_ID_BYTES])
        nonce, sealed = raw[_ID_BYTES : _ID_BYTES + _NONCE_BYTES], raw[_ID_BYTES + _NONCE_BYTES :]
        try:
            return AESGCM(key.secret).decrypt(nonce, sealed, _associated(purpose)).decode()
        except InvalidTag as error:
            raise DecryptError("changed, or stored for something else") from error

    def is_current(self, stored: str) -> bool:
        return secrets.compare_digest(_unwrap(stored)[:_ID_BYTES], self.current.id)

    def reencrypt(self, stored: str, purpose: str) -> str:
        """The value encrypted with the current key (unchanged if it already is)."""
        return stored if self.is_current(stored) else self.encrypt(self.decrypt(stored, purpose), purpose)


def _associated(purpose: str) -> bytes:
    return f"terraforma:{purpose}".encode()


def _unwrap(stored: str) -> bytes:
    if not stored.startswith(PREFIX):
        raise DecryptError("not an encrypted value")
    try:
        raw = _decode(stored[len(PREFIX) :])
    except ValueError as error:
        raise DecryptError("not an encrypted value") from error
    if len(raw) < _ID_BYTES + _NONCE_BYTES + 16:
        raise DecryptError("not an encrypted value")
    return raw


# --- the key files ----------------------------------------------------------

def new_key(key_dir: Path) -> Path:
    """Makes the first current key. Refuses to replace one: that would make stored values unreadable."""
    path = key_path(key_dir, "current")
    if path.exists():
        raise FileExistsError(f"{path} already exists: use `keys rotate` to replace it safely")
    _write_key(path, Key(secrets.token_bytes(KEY_BYTES)))
    return path


def shift_slots(key_dir: Path) -> None:
    """The current key becomes the previous one (the old previous is dropped) and a new current is made.

    Run reencrypt_all first, so nothing still needs the key being dropped.
    """
    current = _read_key(key_path(key_dir, "current"))
    if current is None:
        raise KeysMissing(f"no encryption key in {key_dir} to rotate")
    _write_key(key_path(key_dir, "previous"), current)
    _write_key(key_path(key_dir, "current"), Key(secrets.token_bytes(KEY_BYTES)))


# --- encrypted columns --------------------------------------------------------

@dataclass(frozen=True)
class EncryptedColumn:
    table: Table
    column: str
    purpose: str


ENCRYPTED: list[EncryptedColumn] = []


def encrypted_column(column: Column, purpose: str) -> None:
    """Records that $column holds values encrypted for $purpose, so rotating keys re-encrypts it."""
    ENCRYPTED.append(EncryptedColumn(column.table, column.name, purpose))


async def reencrypt_all(session: AsyncSession, ring: KeyRing, columns: Iterable[EncryptedColumn] | None = None) -> int:
    """Re-encrypts every stored value not made with the current key. Returns how many changed."""
    changed = 0
    for entry in ENCRYPTED if columns is None else columns:
        table, column = entry.table, entry.table.c[entry.column]
        keys = list(table.primary_key.columns)
        rows = await session.execute(select(*keys, column).where(column.is_not(None)))
        for row in rows.all():
            stored = row[-1]
            if ring.is_current(stored):
                continue
            match = [key == value for key, value in zip(keys, row[:-1], strict=True)]
            await session.execute(update(table).where(*match).values({entry.column: ring.reencrypt(stored, entry.purpose)}))
            changed += 1
    return changed
