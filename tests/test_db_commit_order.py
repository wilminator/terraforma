"""A call's changes are committed before its answer is sent, so a browser that reads straight after a write sees it.

FastAPI runs a ``yield`` dependency's exit after the response has gone out unless the dependency has ``scope="function"``; the
commit is in that exit. On a database where a commit takes a while (a disk that syncs), the browser's next read then
arrived first and saw the old data: the player's new hero showed up only after the next one was made.
"""

import pytest
from fastapi import FastAPI

from terraforma.api.deps import Db

pytestmark = pytest.mark.anyio


class Begin:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        self.events.append("begin")

    async def __aexit__(self, *exc):
        self.events.append("commit")


class FakeSession:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def begin(self):
        return Begin(self.events)


async def test_the_commit_happens_before_the_response_is_sent():
    events = []
    app = FastAPI()
    app.state.sessionmaker = lambda: FakeSession(events)

    @app.post("/write")
    async def write(db: Db) -> dict:
        events.append("handler")
        return {"ok": True}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            events.append("response sent")

    scope = {"type": "http", "method": "POST", "path": "/write", "raw_path": b"/write", "query_string": b"", "headers": [], "server": ("test", 80), "client": ("c", 1), "scheme": "http", "http_version": "1.1", "root_path": ""}
    await app(scope, receive, send)
    assert events == ["begin", "handler", "commit", "response sent"]
