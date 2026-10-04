"""Browser tests: the real server on a real port, driven by Chromium through Playwright.

``live_server`` serves the example game on the database under test (SQLite by default; every database listed in
TERRAFORMA_TEST_DATABASES runs the same tests), freshly migrated for each test. ``page`` is a browser page on it.
A test fails on anything the browser would show a player as a problem: a console error or warning, an uncaught error,
a failed request. A test that expects one says so with ``console.allow("text")``.
"""

import asyncio
import threading

import pytest
import uvicorn

from terraforma.accounts.service import create_account
from terraforma.app import create_app
from terraforma.db.session import make_engine
from terraforma.example import GAME
from terraforma.keys import new_key
from terraforma.settings import Settings
from terraforma.testing import SECRET, fresh_database

PASSWORD = "correct horse battery"


class LiveServer:
    """The app served by uvicorn on a free port, in a thread with its own event loop (the test itself is plain, synchronous code)."""

    def __init__(self, database_url, key_dir, mailbox, game=GAME):
        self.database_url = database_url
        self.settings = Settings(
            database_url=database_url,
            session_secret=SECRET,
            secure_cookies=False,
            public_url="http://127.0.0.1",
            key_dir=key_dir,
            fight_timer_seconds=0,
            housekeeping_seconds=0,
        )
        self.game = game
        self.mailbox = mailbox
        self.url = ""
        self._loop = None
        self._server = None
        self._started = threading.Event()
        self._failure = None
        self._thread = threading.Thread(target=self._run, name="live-server", daemon=True)

    def start(self):
        self._thread.start()
        if not self._started.wait(60):
            raise RuntimeError("the live server did not start in 60 seconds")
        if self._failure:
            raise RuntimeError("the live server failed to start") from self._failure
        return self

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        self._thread.join(30)

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            self._failure = error
            self._started.set()

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        engine = make_engine(self.database_url)
        await fresh_database(engine, self.database_url)
        await engine.dispose()
        app = create_app(self.settings, self.game, mailer=self.mailbox)
        self.app = app
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        serving = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if serving.done():
                await serving  # raises why it stopped
            await asyncio.sleep(0.02)
        self.url = f"http://127.0.0.1:{self._server.servers[0].sockets[0].getsockname()[1]}"
        self._started.set()
        await serving

    def in_db(self, work):
        """Runs async $work(session) against the app's database, committed (on the server's own event loop)."""

        async def go():
            async with self.app.state.sessionmaker() as session:
                async with session.begin():
                    return await work(session)

        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(30)

    def make_account(self, username="Mike", password=PASSWORD):
        """A confirmed account the test can log in to."""
        return self.in_db(lambda db: create_account(db, username, password, email=f"{username.lower()}@example.com", confirmed=True))


@pytest.fixture
def live_server(database_url, mailbox, tmp_path):
    new_key(tmp_path / "keys")
    server = LiveServer(database_url, tmp_path / "keys", mailbox).start()
    yield server
    server.stop()


@pytest.fixture
def browser(live_server):
    """Chromium, started after the live server is up: Playwright's synchronous API keeps an event loop running in this thread,
    and the database fixtures (terraforma.testing) start their own with asyncio.run, which cannot while one is running."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(args=["--disable-dev-shm-usage"])  # Docker's /dev/shm is tiny
        yield chromium
        chromium.close()


class Console:
    """What the browser reported as a problem during a test."""

    def __init__(self):
        self.problems = []
        self._allowed = []

    def allow(self, text):
        """Says this test expects a problem containing $text (a failed login makes the browser log the 401, for one)."""
        self._allowed.append(text)

    def unexpected(self):
        return [each for each in self.problems if not any(text in each for text in self._allowed)]


@pytest.fixture
def console():
    return Console()


@pytest.fixture
def new_page(browser, live_server, console):
    """Opens a page on the live server; $viewport sets the window (landscape by default)."""
    contexts = []

    def open_page(viewport=None):
        context = browser.new_context(viewport=viewport or {"width": 1280, "height": 720}, base_url=live_server.url)
        contexts.append(context)
        page = context.new_page()
        page.on("console", lambda message: console.problems.append(f"console {message.type}: {message.text}") if message.type in ("error", "warning") else None)
        page.on("pageerror", lambda error: console.problems.append(f"uncaught: {error}"))
        page.on("requestfailed", lambda request: console.problems.append(f"request failed: {request.url}"))
        return page

    yield open_page
    for context in contexts:
        context.close()
    assert console.unexpected() == [], "the browser reported problems"


@pytest.fixture
def page(new_page):
    return new_page()
