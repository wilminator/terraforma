"""Engine commands, run in the container:

    python -m terraforma serve [GAME_APP]   check settings, migrate, then serve (default: the development app)
    python -m terraforma migrate            bring the database's tables up to date
    python -m terraforma check-settings     read settings.toml and the keys and say what's wrong, if anything
    python -m terraforma keys new           make the first encryption key
    python -m terraforma keys rotate        replace the key, re-encrypting everything stored with it

GAME_APP is a game's app factory, such as ``my_game:app``.
"""

import asyncio
import sys

from . import keys, models  # noqa: F401  (models: every encrypted column is registered)
from .db.migrate import upgrade
from .db.session import make_engine, make_sessionmaker
from .settings import load_settings

DEFAULT_APP = "terraforma.devserver:app"


async def migrate() -> None:
    engine = make_engine(load_settings().database_url)
    try:
        await upgrade(engine)
    finally:
        await engine.dispose()


def check_settings() -> bool:
    settings = load_settings()
    print(f"Settings OK. Database: {settings.database_url.split('://', 1)[0]}; mail: {'on' if settings.mail else 'off'}.")
    try:
        ring = keys.KeyRing.load(settings.key_dir)
    except (keys.KeysMissing, keys.KeyFileError) as error:
        print(f"Keys: {error}")
        return False
    print(f"Keys OK: current{' and previous' if ring.previous else ''} in {settings.key_dir}.")
    return True


async def reencrypt(ring: keys.KeyRing) -> int:
    engine = make_engine(load_settings().database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            async with session.begin():
                return await keys.reencrypt_all(session, ring)
    finally:
        await engine.dispose()


def manage_keys(action: str) -> int:
    key_dir = load_settings().key_dir
    try:
        return _manage_keys(action, key_dir)
    except (keys.KeysMissing, keys.KeyFileError) as error:
        print(error)
        return 1


def _manage_keys(action: str, key_dir) -> int:
    if action == "new":
        try:
            print(f"Made {keys.new_key(key_dir)}. Back it up apart from the database.")
        except FileExistsError as error:
            print(error)
            return 1
        return 0
    if action == "rotate":
        # Anything still on the previous key moves to the current one first, so dropping it loses nothing;
        # then the slots shift and everything moves to the new key. Stopped halfway, both slots still read it all.
        asyncio.run(migrate())
        asyncio.run(reencrypt(keys.KeyRing.load(key_dir)))
        keys.shift_slots(key_dir)
        changed = asyncio.run(reencrypt(keys.KeyRing.load(key_dir)))
        print(f"Rotated the key in {key_dir} and re-encrypted {changed} stored values. Back up the new key.")
        return 0
    print(__doc__)
    return 2


def serve(factory: str) -> None:
    import uvicorn

    if not check_settings():
        sys.exit(1)
    asyncio.run(migrate())
    print("Database is up to date.")
    # Behind the NAS's HTTPS proxy: trust its X-Forwarded-For / -Proto.
    uvicorn.run(factory, factory=True, host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="*")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "migrate":
        asyncio.run(migrate())
        print("Database is up to date.")
        return 0
    if command == "check-settings":
        return 0 if check_settings() else 1
    if command == "keys":
        return manage_keys(argv[2] if len(argv) > 2 else "")
    if command == "serve":
        serve(argv[2] if len(argv) > 2 else DEFAULT_APP)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
