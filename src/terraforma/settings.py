"""Settings, read from a TOML file.

The NAS the game runs on may not pass environment variables or Docker
secrets to the container, so everything comes from one file:
``settings.toml`` in the working directory, or the path in
``TERRAFORMA_SETTINGS`` where the environment does work.

    database_url = "postgresql+asyncpg://terraforma:secret@db/terraforma"
    session_secret = "a long random string"
    secure_cookies = true
"""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # SQLAlchemy async URL: postgresql+asyncpg://, mysql+aiomysql:// or
    # sqlite+aiosqlite:///path.db
    database_url: str = "sqlite+aiosqlite:///terraforma.db"
    # Signs the session cookie. At least 32 characters.
    session_secret: str = Field(min_length=32)
    # Send the session cookie over HTTPS only. Off only for local dev.
    secure_cookies: bool = True
    # How long a login lasts, in seconds.
    session_max_age: int = 60 * 60 * 24 * 14


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("TERRAFORMA_SETTINGS", "settings.toml"))
    with path.open("rb") as file:
        return Settings(**tomllib.load(file))
