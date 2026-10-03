"""Settings, read from a TOML file.

The NAS the game runs on may not pass environment variables or Docker
secrets to the container, so everything comes from one file:
``settings.toml`` in the working directory, or the path in
``TERRAFORMA_SETTINGS`` where the environment does work.

    database_url = "postgresql+asyncpg://terraforma:secret@db/terraforma"
    session_secret = "a long random string"
    secure_cookies = true
    key_dir = "keys"

    [mail]
    host = "smtp.example.com"
    username = "..."
    password = "..."
    from_address = "Vanguard Tavern <noreply@example.com>"
"""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MailSettings(BaseModel):
    """Outgoing mail (Cloudflare: port 465, implicit TLS, an API key as username and password)."""

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 465
    # TLS from the first byte (port 465), rather than STARTTLS (port 587).
    implicit_tls: bool = True
    username: str
    password: str
    from_address: str


class Settings(BaseModel):
    # A misspelled setting is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    # SQLAlchemy async URL: postgresql+asyncpg://, mysql+aiomysql:// or
    # sqlite+aiosqlite:///path.db
    database_url: str = "sqlite+aiosqlite:///terraforma.db"
    # Signs the session cookie. At least 32 characters.
    session_secret: str = Field(min_length=32)
    # Send the session cookie over HTTPS only. Off only for local dev.
    secure_cookies: bool = True
    # How long a login lasts, in seconds.
    session_max_age: int = 60 * 60 * 24 * 14
    # The address players reach the game at, for links in emails.
    public_url: str = "http://localhost:8000"
    # Development without [mail]: messages are written here as .eml files.
    outbox_dir: Path = Path("outbox")
    # Where the encryption key files live (two slots: current and previous).
    key_dir: Path = Path("keys")
    # No [mail] section: the game sends no mail (fine for development).
    mail: MailSettings | None = None
    # How often the server looks for fights whose round time has run out, in seconds (0: never, for tests).
    fight_timer_seconds: float = Field(default=1.0, ge=0)
    # How often the server tidies up what has gone stale (housekeeping jobs), in seconds (0: never, for tests).
    housekeeping_seconds: float = Field(default=3600.0, ge=0)
    # How long a pending drop (need/want or hand-out) may wait for its players before housekeeping settles it with the
    # answers so far, in seconds (0: never, the default).
    pending_drop_timeout_seconds: float = Field(default=0.0, ge=0)
    # Housekeeping removes closed ballots and settled rating prompts older than this many days (0: keep them for good).
    ballot_retention_days: int = Field(default=0, ge=0)
    rating_prompt_retention_days: int = Field(default=0, ge=0)
    # A game that sells Challenge Tokens sets this (at least 32 characters) and its backend credits purchases with
    # POST /api/server/challenge/purchase, sending it as "Authorization: Bearer <secret>". Empty: the call is off.
    challenge_purchase_secret: str = ""

    @field_validator("challenge_purchase_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("challenge_purchase_secret must be at least 32 characters (or empty, to turn the call off)")
        return value


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("TERRAFORMA_SETTINGS", "settings.toml"))
    with path.open("rb") as file:
        return Settings(**tomllib.load(file))
