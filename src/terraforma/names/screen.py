"""Is this name safe to show other players? The check is the game's ``Rules.screen_name``; by default it asks the sign-in provider's name
check (``ExternalProvider.name_screen``), which this module carries (``NameScreen``: HTTP, cache, breaker). The engine ships no word list.

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

import httpx2 as httpx

from ..fights.rules import Rules
from ..settings import ExternalProvider
from .display import public_name  # noqa: F401  (``public_name`` has always been importable from here)

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
    """What the name-taking calls use: the app's rules (``Rules.screen_name`` is the check) and the provider's name transport, which may be missing."""

    def __init__(self, rules: Rules, remote: NameScreen | None = None):
        self.rules = rules
        self.remote = remote


def build_screener(settings, game, http: Callable[[], httpx.AsyncClient]) -> Screener:
    """The app's screener: the game's rules (the engine's own without a game, as ``get_rules`` has it) and, when a provider has ``name_screen`` on, its name check."""
    rules = game.rules if game is not None else Rules()
    provider = next((each for each in settings.external_providers if each.name_screen), None)
    return Screener(rules, NameScreen(provider, http) if provider is not None else None)


@dataclass(frozen=True)
class Screened:
    """What the screen said about a name. $name_ok: what ``Rules.screen_name`` answered (False: unsafe, True: fine, None: not checked, which
    is also the answer with no screener). $list_changed: the word-list time the provider's answer was given under (the ``screened_at_list``
    stamp), or None when the provider gave no answer during this check."""

    name_ok: bool | None = None
    list_changed: str | None = None


async def screen_name(screen: Screener | None, name: str) -> Screened:
    """Never refuses: the caller saves the name and stores both fields (an exception from the game's ``Rules.screen_name`` is its bug and
    propagates). Call it after the format check."""
    if screen is None:
        return Screened()
    answered = False

    async def ask_provider(asked: str) -> bool | None:
        nonlocal answered
        if screen.remote is None:
            return None
        verdict = await screen.remote.check(asked)
        if verdict is Verdict.UNKNOWN:
            return None
        answered = True
        return verdict is Verdict.OK

    name_ok = await screen.rules.screen_name(name, ask_provider)
    return Screened(name_ok, screen.remote.list_changed if answered and screen.remote is not None else None)
