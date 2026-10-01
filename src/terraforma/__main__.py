"""Engine commands, run in the container:

    python -m terraforma serve [GAME_APP]   check settings, migrate, then serve (default: the development app)
    python -m terraforma migrate            bring the database's tables up to date
    python -m terraforma check-settings     read settings.toml and say what's wrong, if anything

GAME_APP is a game's app factory, such as ``my_game:app``.
"""

import asyncio
import sys

from .db.migrate import upgrade
from .db.session import make_engine
from .settings import load_settings

DEFAULT_APP = "terraforma.devserver:app"


async def migrate() -> None:
    engine = make_engine(load_settings().database_url)
    try:
        await upgrade(engine)
    finally:
        await engine.dispose()


def check_settings() -> None:
    settings = load_settings()
    print(f"Settings OK. Database: {settings.database_url.split('://', 1)[0]}; mail: {'on' if settings.mail else 'off'}.")


def serve(factory: str) -> None:
    import uvicorn

    check_settings()
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
        check_settings()
        return 0
    if command == "serve":
        serve(argv[2] if len(argv) > 2 else DEFAULT_APP)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
