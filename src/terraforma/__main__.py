"""Engine commands, run in the container:

    python -m terraforma serve [GAME_APP]   check settings, make the tables, then serve (default: the development app)
    python -m terraforma create-tables      make the tables in an empty database (serve does this too)
    python -m terraforma reset-database --yes   DELETE every table and make them again (development: the models changed)
    python -m terraforma check-settings     read settings.toml and the keys and say what's wrong, if anything
    python -m terraforma keys new           make the first encryption key
    python -m terraforma keys rotate        replace the key, re-encrypting everything stored with it
    python -m terraforma export-state FILE [--live] [GAME_APP]   write the planned (or --live) settings state to FILE
    python -m terraforma import-state FILE [GAME_APP]            put a file's state in the draft, frozen (live is untouched)

GAME_APP is a game's app factory, such as ``my_game:app``.
"""

import asyncio
import json
import sys
from pathlib import Path

from . import keys, models  # noqa: F401  (models: every encrypted column is registered)
from .db.schema import SchemaOutdated, ensure_schema, reset_schema
from .db.session import make_engine, make_sessionmaker
from .settings import load_settings

DEFAULT_APP = "terraforma.devserver:app"


async def create_tables(reset: bool = False) -> None:
    engine = make_engine(load_settings().database_url)
    try:
        await (reset_schema if reset else ensure_schema)(engine)
    finally:
        await engine.dispose()


async def run_state_command(app, action: str, path: Path, live: bool = False) -> int:
    """Exports the app's planned (or live) state to $path, or imports $path into its draft (frozen). The app is built, not started."""
    from .gamestate import service, transfer

    sections, modules = app.state.state_sections, [addon.key for addon in app.state.addons]
    engine = make_engine(app.state.settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            if action == "export-state":
                data = await transfer.export(session, sections, modules, "live" if live else "planned")
                path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(f"Wrote the {data['source']} state ({data['hash'][:12]}) to {path}.")
                return 0
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                current = await transfer.import_file(session, sections, modules, data, None)
            except (OSError, ValueError) as error:  # (not a file, or not JSON)
                print(f"Can't read {path}: {error}")
                return 1
            except service.Refused as error:
                print(f"{error}:")
                for problem in error.problems:
                    print(f"  - {problem.section + ': ' if problem.section else ''}{problem.message}")
                return 1
            except service.StateError as error:
                print(error)
                return 1
            print(f"Imported into the draft, frozen ({current.planned_hash[:12]}). Live is unchanged until the draft is committed.")
            return 0
    finally:
        await engine.dispose()


def state_command(action: str, args: list[str]) -> int:
    from uvicorn.importer import import_from_string

    flags = [each for each in args if each.startswith("--")]
    words = [each for each in args if not each.startswith("--")]
    if not words or len(words) > 2 or set(flags) - {"--live"} or (action == "import-state" and flags):
        print(__doc__)
        return 2
    try:
        asyncio.run(create_tables())
    except SchemaOutdated as error:
        print(error)
        return 1
    try:
        app = import_from_string(words[1] if len(words) > 1 else DEFAULT_APP)()
    except Exception as error:  # (a game's factory fails in its own ways; say which)
        print(f"Can't build the app {words[1] if len(words) > 1 else DEFAULT_APP}: {error}")
        return 1
    return asyncio.run(run_state_command(app, action, Path(words[0]), "--live" in flags))


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
        asyncio.run(create_tables())
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
    try:
        asyncio.run(create_tables())
    except SchemaOutdated as error:
        print(error)
        sys.exit(1)
    print("Database is ready.")
    # Behind the NAS's HTTPS proxy: trust its X-Forwarded-For / -Proto.
    uvicorn.run(factory, factory=True, host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="*")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "create-tables":
        try:
            asyncio.run(create_tables())
        except SchemaOutdated as error:
            print(error)
            return 1
        print("Database is ready.")
        return 0
    if command == "reset-database":
        if "--yes" not in argv[2:]:
            print("This deletes every table in the database. Run it again with --yes to go ahead.")
            return 2
        asyncio.run(create_tables(reset=True))
        print("Database reset.")
        return 0
    if command == "check-settings":
        return 0 if check_settings() else 1
    if command == "keys":
        return manage_keys(argv[2] if len(argv) > 2 else "")
    if command in ("export-state", "import-state"):
        return state_command(command, argv[2:])
    if command == "serve":
        serve(argv[2] if len(argv) > 2 else DEFAULT_APP)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
