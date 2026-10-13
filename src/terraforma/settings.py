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

    [[external_providers]]
    name = "google"
    label = "Google"
    issuer = "https://accounts.google.com"
    client_id = "..."
    client_secret = "..."
"""

import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


def _web_address(value: str, setting: str) -> str:
    """$value if it is an https address (http only for this computer, for development)."""
    parts = urlsplit(value)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError(f"{setting} must be an https address (http is accepted only for localhost)")
    if not parts.hostname or parts.fragment:
        raise ValueError(f"{setting} is not a valid address")
    return value


class ExternalProvider(BaseModel):
    """One OpenID Connect provider players may sign in with (Google, Microsoft, ...).

    The address to register at the provider as the redirect address is ``<public_url>/api/external/<name>/callback``.
    """

    model_config = ConfigDict(extra="forbid")

    # Appears in addresses and in the database: lowercase letters, digits, _ and -.
    name: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    # What the sign-in button says.
    label: str = Field(min_length=1, max_length=64)
    # The provider's issuer address; its settings are found at <issuer>/.well-known/openid-configuration.
    issuer: str
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    scopes: list[str] = ["openid", "email"]
    # Both set: used instead of the provider's published settings (for a provider that publishes none).
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None

    @field_validator("issuer", "authorization_endpoint", "token_endpoint")
    @classmethod
    def _is_a_web_address(cls, value: str | None, info) -> str | None:
        return None if value is None else _web_address(value, info.field_name)

    @field_validator("scopes")
    @classmethod
    def _asks_for_openid_and_email(cls, value: list[str]) -> list[str]:
        if "openid" not in value or "email" not in value:
            raise ValueError('scopes must include "openid" and "email"')
        return value

    @model_validator(mode="after")
    def _endpoints_come_together(self) -> "ExternalProvider":
        if (self.authorization_endpoint is None) != (self.token_endpoint is None):
            raise ValueError("authorization_endpoint and token_endpoint are set together or not at all")
        return self


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
    # Housekeeping removes chat messages older than this many days, except ones a player flagged that no admin has settled (0: keep them for good).
    chat_retention_days: int = Field(default=7, ge=0)
    # Voice through Discord: a bot token and the id of the Discord server (guild) whose voice channels the game may make. Both, or voice is off.
    discord_bot_token: str = ""
    discord_guild_id: str = Field(default="", pattern=r"^(\d{1,24})?$")
    # Housekeeping closes a voice room this many hours after it was made (players ask for a new one when they next join).
    voice_room_hours: int = Field(default=24, ge=1)
    # A game that sells Challenge Tokens sets this (at least 32 characters) and its backend credits purchases with
    # POST /api/server/challenge/purchase, sending it as "Authorization: Bearer <secret>". Empty: the call is off.
    challenge_purchase_secret: str = ""
    # Providers a player may sign in with instead of a password (each a [[external_providers]] section).
    external_providers: list[ExternalProvider] = []

    @field_validator("external_providers")
    @classmethod
    def _names_are_unique(cls, value: list[ExternalProvider]) -> list[ExternalProvider]:
        names = [provider.name for provider in value]
        if len(names) != len(set(names)):
            raise ValueError("two external_providers share a name")
        return value

    # A game whose store grants Challenge Tokens and items sets this (at least 32 characters) and the store's backend calls
    # POST /api/server/store/grant, sending it as "Authorization: Bearer <secret>". Empty: the call is off.
    store_grant_secret: str = ""

    @field_validator("challenge_purchase_secret", "store_grant_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: str, info) -> str:
        if value and len(value) < 32:
            raise ValueError(f"{info.field_name} must be at least 32 characters (or empty, to turn the call off)")
        return value


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("TERRAFORMA_SETTINGS", "settings.toml"))
    with path.open("rb") as file:
        return Settings(**tomllib.load(file))
