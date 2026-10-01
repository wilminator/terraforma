"""Encryption keys in two slots: values made with either read back, rotating loses nothing."""

import stat

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select

from terraforma import keys
from terraforma.__main__ import main
from terraforma.keys import DecryptError, KeyRing, KeysMissing, new_key, reencrypt_all, shift_slots


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "keys"
    new_key(path)
    return path


def test_a_value_reads_back_and_is_not_stored_in_the_clear(key_dir):
    ring = KeyRing.load(key_dir)
    stored = ring.encrypt("JBSWY3DPEHPK3PXP", "accounts.totp_secret")
    assert "JBSWY3DPEHPK3PXP" not in stored
    assert stored != ring.encrypt("JBSWY3DPEHPK3PXP", "accounts.totp_secret"), "a fresh nonce every time"
    assert ring.decrypt(stored, "accounts.totp_secret") == "JBSWY3DPEHPK3PXP"


def test_a_value_only_reads_for_the_purpose_it_was_stored_for(key_dir):
    ring = KeyRing.load(key_dir)
    stored = ring.encrypt("secret", "accounts.totp_secret")
    with pytest.raises(DecryptError):
        ring.decrypt(stored, "accounts.recovery_codes")


def test_a_changed_value_is_refused(key_dir):
    ring = KeyRing.load(key_dir)
    stored = ring.encrypt("secret", "p")
    tampered = stored[:-2] + ("A" if stored[-2] != "A" else "B") + stored[-1]
    for bad in (tampered, "secret", "tf1.short", "tf1.!!!!"):
        with pytest.raises(DecryptError):
            ring.decrypt(bad, "p")


def test_the_server_needs_a_current_key(tmp_path):
    with pytest.raises(KeysMissing):
        KeyRing.load(tmp_path / "keys")


def test_a_key_file_is_private_and_never_overwritten_by_new(key_dir):
    path = key_dir / "current.key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_text()
    with pytest.raises(FileExistsError):
        new_key(key_dir)
    assert path.read_text() == before


def test_a_broken_key_file_is_reported(key_dir):
    (key_dir / "current.key").write_text("not a key\n")
    with pytest.raises(keys.KeyFileError):
        KeyRing.load(key_dir)


def test_after_rotating_old_values_still_read_and_new_ones_use_the_new_key(key_dir):
    old = KeyRing.load(key_dir)
    stored = old.encrypt("secret", "p")
    shift_slots(key_dir)
    ring = KeyRing.load(key_dir)
    assert ring.previous == old.current
    assert ring.decrypt(stored, "p") == "secret", "the previous slot still reads it"
    assert not ring.is_current(stored)
    moved = ring.reencrypt(stored, "p")
    assert ring.is_current(moved) and ring.decrypt(moved, "p") == "secret"
    assert ring.reencrypt(moved, "p") == moved, "already current: left alone"
    shift_slots(key_dir)
    with pytest.raises(DecryptError):  # two rotations on, a value never re-encrypted can't be read
        KeyRing.load(key_dir).decrypt(stored, "p")


# --- re-encrypting what's stored, on every database ------------------------

SECRETS = Table("test_secrets", MetaData(), Column("id", Integer, primary_key=True), Column("value", String(200)))
COLUMN = keys.EncryptedColumn(SECRETS, "value", "test.value")


@pytest.mark.anyio
async def test_rotating_re_encrypts_every_stored_value(engine, db, key_dir):
    async with engine.begin() as connection:
        await connection.run_sync(SECRETS.metadata.create_all)
    try:
        old = KeyRing.load(key_dir)
        await db.execute(insert(SECRETS), [
            {"id": 1, "value": old.encrypt("one", "test.value")},
            {"id": 2, "value": old.encrypt("two", "test.value")},
            {"id": 3, "value": None},
        ])
        shift_slots(key_dir)
        ring = KeyRing.load(key_dir)
        assert await reencrypt_all(db, ring, [COLUMN]) == 2
        assert await reencrypt_all(db, ring, [COLUMN]) == 0, "nothing left on the old key"
        rows = (await db.execute(select(SECRETS.c.id, SECRETS.c.value).order_by(SECRETS.c.id))).all()
        assert [ring.decrypt(value, "test.value") for _, value in rows[:2]] == ["one", "two"]
        assert all(ring.is_current(value) for _, value in rows[:2])
        assert rows[2].value is None
        await db.commit()
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(SECRETS.metadata.drop_all)


def test_the_keys_commands(tmp_path, monkeypatch, capsys):
    path = tmp_path / "settings.toml"
    path.write_text(f'database_url = "sqlite+aiosqlite:///{tmp_path}/game.db"\nsession_secret = "' + "x" * 32 + '"\n'
                    f'key_dir = "{tmp_path}/keys"\n')
    monkeypatch.setenv("TERRAFORMA_SETTINGS", str(path))
    assert main(["terraforma", "keys", "rotate"]) == 1, "nothing to rotate yet"
    assert main(["terraforma", "keys", "new"]) == 0
    assert main(["terraforma", "keys", "new"]) == 1, "never replaces a key"
    first = KeyRing.load(tmp_path / "keys").current
    assert main(["terraforma", "keys", "rotate"]) == 0
    ring = KeyRing.load(tmp_path / "keys")
    assert ring.previous == first and ring.current != first
    assert main(["terraforma", "keys", "nonsense"]) == 2
