"""Is this name safe to show other players? Two judges, either of which can say no: the game's own word filter (``Game.name_screen``;
the engine ships no word list) and, when the settings turn it on, the sign-in provider's name check (``ExternalProvider.name_screen``).

A name is never refused. ``screen_name`` says whether it passed (``Screened.name_ok``) and the caller stores that beside the name;
``public_name`` then hides a name that failed from everybody but its owner.

The provider's check is fail-open: a name is marked not ok only when somebody says it is unsafe. A provider that is down, slow, rate
limiting us or misconfigured makes the answer ``UNKNOWN`` (``name_ok`` None: not checked). The text of a name is sent to the
provider and nowhere else; it is never logged.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import httpx2 as httpx

from ..settings import ExternalProvider

log = logging.getLogger("terraforma.names")

TIMEOUT = 2.0  # seconds; no retries in the request path
CACHE_SECONDS = 300
CACHE_ENTRIES = 2000
BREAKER_FAILURES = 5  # straight failures that open the breaker
BREAKER_SECONDS = 60
CALLS_PER_MINUTE = 300
LIST_EVERY = 60  # GET /list at most this often
AUTH_LOG_EVERY = 3600  # one error line per hour for 401/403
RETRY_AFTER_MAX = 3600


class Verdict(Enum):
    OK = "ok"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class NameFilter(Protocol):
    """A game's own word filter (``Game.name_screen``). Public interface: the license exception covers it."""

    def is_unsafe(self, name: str) -> bool: ...


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


class NameScreen:
    """Asks one provider's Accounts name API (``POST <name_api>/check``). $http is the shared client, or a function that returns it
    (the app's client may be replaced); $clock is a monotonic clock in seconds (tests pass their own)."""

    def __init__(self, provider: ExternalProvider, http: httpx.AsyncClient | Callable[[], httpx.AsyncClient], clock: Callable[[], float] = time.monotonic):
        self.provider = provider.name
        self._base = (provider.name_api or f"{provider.issuer.rstrip('/')}/api/names").rstrip("/")
        self._auth = httpx.BasicAuth(provider.client_id, provider.client_secret)
        self._http = http if callable(http) else (lambda: http)
        self._clock = clock
        self._list_changed: str | None = None
        self._cache: dict[str, tuple[float, Verdict]] = {}
        self._failures = 0
        self._blocked_until = 0.0
        self._calls: deque[float] = deque()
        self._auth_logged: float | None = None
        self._list_asked: float | None = None

    @property
    def list_changed(self) -> str | None:
        """The newest word-list time seen (the times sort as text)."""
        return self._list_changed

    def _seen(self, list_changed: object) -> None:
        if isinstance(list_changed, str) and list_changed and (self._list_changed is None or list_changed > self._list_changed):
            self._list_changed = list_changed
            self._cache.clear()  # every answer given under the old list is void

    def _failed(self, now: float, why: str, retry_after: float = 0) -> None:
        self._failures += 1
        if retry_after:
            self._blocked_until = max(self._blocked_until, now + retry_after)
        if self._failures >= BREAKER_FAILURES:
            if now >= self._blocked_until:
                log.warning("name check by %s: %s; %d failures in a row, not asking for %d s", self.provider, why, self._failures, BREAKER_SECONDS)
            self._blocked_until = max(self._blocked_until, now + BREAKER_SECONDS)
        else:
            log.info("name check by %s failed: %s", self.provider, why)

    async def _send(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """The provider's JSON answer, or None (blocked, over the cap, or failed: all of which mean "unknown")."""
        now = self._clock()
        if now < self._blocked_until:
            return None
        while self._calls and self._calls[0] <= now - 60:
            self._calls.popleft()
        if len(self._calls) >= CALLS_PER_MINUTE:
            log.warning("name check by %s: over %d calls a minute, answering unknown", self.provider, CALLS_PER_MINUTE)
            return None
        self._calls.append(now)
        try:
            response = await self._http().request(method, f"{self._base}{path}", json=body, auth=self._auth, timeout=TIMEOUT)
        except httpx.HTTPError as error:
            self._failed(now, type(error).__name__)
            return None
        code = response.status_code
        if code == 429:
            try:
                wait = min(max(float(response.headers.get("Retry-After", "")), 0), RETRY_AFTER_MAX)
            except ValueError:
                wait = BREAKER_SECONDS
            self._failed(now, "rate limited (429)", wait)
            return None
        if code in (401, 403):
            if self._auth_logged is None or now - self._auth_logged >= AUTH_LOG_EVERY:
                self._auth_logged = now
                log.error("name check by %s was refused (%d): check client_id and client_secret in settings.toml; names are being accepted unchecked", self.provider, code)
            self._failed(now, f"refused ({code})")
            return None
        if code != 200:
            self._failed(now, f"status {code}")
            return None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._failed(now, "unreadable answer")
            return None
        self._failures = 0
        self._seen(data.get("list_changed"))
        return data

    async def check(self, name: str) -> Verdict:
        key = _key(name)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        data = await self._send("POST", "/check", {"name": name})
        if data is None or not isinstance(data.get("safe"), bool):
            if data is not None:
                self._failed(now, "unreadable answer")
            return Verdict.UNKNOWN
        verdict = Verdict.OK if data["safe"] else Verdict.UNSAFE
        if len(self._cache) >= CACHE_ENTRIES:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            while len(self._cache) >= CACHE_ENTRIES:
                del self._cache[next(iter(self._cache))]
        self._cache[key] = (now + CACHE_SECONDS, verdict)
        return verdict

    async def refresh_list(self) -> str | None:
        """Asks when the word list last changed (at most once a minute) and returns the newest time known."""
        now = self._clock()
        if self._list_asked is None or now - self._list_asked >= LIST_EVERY:
            self._list_asked = now
            await self._send("GET", "/list")
        return self._list_changed


class Screener:
    """What the name-taking calls use: the game's filter and the provider's check, either of which may be missing."""

    def __init__(self, local: NameFilter | None = None, remote: NameScreen | None = None):
        self.local = local
        self.remote = remote


def build_screener(settings, game, http: Callable[[], httpx.AsyncClient]) -> Screener | None:
    """The app's screener: none at all unless the game has a filter or a provider has ``name_screen`` on."""
    local = getattr(game, "name_screen", None) if game is not None else None
    provider = next((each for each in settings.external_providers if each.name_screen), None)
    remote = NameScreen(provider, http) if provider is not None else None
    return Screener(local, remote) if local is not None or remote is not None else None


@dataclass(frozen=True)
class Screened:
    """What the screen said about a name. $name_ok: False (somebody said unsafe), True (the provider said ok and the game's filter did not
    object) or None (not checked: no screener, no provider, or an unknown answer). $list_changed: the word-list time the provider's
    answer was given under (the ``screened_at_list`` stamp), or None when there was no such answer or the game's filter said no first."""

    name_ok: bool | None = None
    list_changed: str | None = None


async def screen_name(screen: Screener | None, name: str) -> Screened:
    """Never raises and never refuses: the caller saves the name and stores both fields. Call it after the format check."""
    if screen is None:
        return Screened()
    if screen.local is not None and screen.local.is_unsafe(name):
        return Screened(False)
    if screen.remote is None:
        return Screened()
    verdict = await screen.remote.check(name)
    if verdict is Verdict.UNSAFE:
        return Screened(False, screen.remote.list_changed)
    if verdict is Verdict.OK:
        return Screened(True, screen.remote.list_changed)
    return Screened()


def public_name(name: str, name_ok: bool | None, fallback: str, *, own: bool = False) -> str:
    """The name to show a viewer: $name for its owner ($own) or when nobody called it unsafe (``name_ok`` is not False), else $fallback."""
    return name if own or name_ok is not False else fallback
