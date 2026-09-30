"""Engine commands, run in the container:

    python -m terraforma migrate          bring the database's tables up to date
    python -m terraforma check-settings   read settings.toml and say what's wrong, if anything
"""

import asyncio
import sys

from .db.migrate import upgrade
from .db.session import make_engine
from .settings import load_settings


async def migrate() -> None:
    engine = make_engine(load_settings().database_url)
    try:
        await upgrade(engine)
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "migrate":
        asyncio.run(migrate())
        print("Database is up to date.")
        return 0
    if command == "check-settings":
        settings = load_settings()
        print(f"Settings OK. Database: {settings.database_url.split('://', 1)[0]}; mail: {'on' if settings.mail else 'off'}.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
