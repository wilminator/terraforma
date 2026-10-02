"""The web app: the engine's server calls and the fight WebSocket.

Each call is its own route with a strict Pydantic model for its
arguments, so a call with the wrong shape is refused before any game code
runs. The account calls are in accounts/routes.py.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __doc__ as ENGINE
from . import logscrub
from .accounts.routes import router as account_router
from .accounts.twofa_routes import router as twofa_router
from .accounts.tokens import Tokens
from .api.security import TolerantSessionMiddleware
from .db.session import make_engine, make_sessionmaker
from .fights import live
from .fights.channels import FightChannels
from .fights.routes import economy_of, fight_socket, relations_of, rules_of
from .fights.routes import router as fights_router
from .content.loader import load_content
from .game import Game
from .heroes.inventory_routes import router as inventory_router
from .heroes.routes import router as heroes_router
from .alliances.ballot_routes import router as ballots_router
from .alliances.routes import router as alliances_router
from .profiles.routes import router as profiles_router
from .relations.rating_routes import router as ratings_router
from .relations.routes import router as relations_router
from .towns.routes import router as towns_router
from .trading.routes import router as trading_router
from . import housekeeping
from .keys import KeyRing
from .mail import Mailer, OutboxMailer, SmtpMailer
from .seed import load_seed
from .settings import Settings


def default_mailer(settings: Settings) -> Mailer:
    if settings.mail is not None:
        return SmtpMailer(settings.mail)
    return OutboxMailer(settings.outbox_dir)


async def fight_timer(app: FastAPI, seconds: float) -> None:
    """Plays the rounds whose time has run out, every $seconds, for as long as the app runs."""
    while True:
        await asyncio.sleep(seconds)
        try:
            await live.resolve_overdue(app.state.sessionmaker, rules_of(app), economy_of(app), app.state.fights, relations_of(app))
        except Exception:  # one bad pass must not stop the timer
            logging.getLogger(__name__).exception("playing the overdue fight rounds failed")


async def housekeeping_timer(app: FastAPI, seconds: float) -> None:
    """Runs the housekeeping jobs every $seconds, for as long as the app runs."""
    while True:
        await asyncio.sleep(seconds)
        await housekeeping.run(app.state.sessionmaker)


def create_app(settings: Settings, game: Game | None = None, *, mailer: Mailer | None = None) -> FastAPI:
    logscrub.install()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # No key, no start: better than running on and failing at the first 2FA setup.
        app.state.keys = KeyRing.load(settings.key_dir)
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        if game and game.seed_dir:
            # A seed that doesn't check out stops the server here, naming what's wrong.
            async with app.state.sessionmaker() as session, session.begin():
                await load_content(session, load_seed(game.seed_dir), game.rules.stats, game.rules.resource_names, game.rules.drop_chance_scale)
        timer = asyncio.create_task(fight_timer(app, settings.fight_timer_seconds)) if settings.fight_timer_seconds else None
        tidier = asyncio.create_task(housekeeping_timer(app, settings.housekeeping_seconds)) if settings.housekeeping_seconds else None
        yield
        for task in (timer, tidier):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await engine.dispose()

    app = FastAPI(title=game.name if game else "TerraForma", lifespan=lifespan)
    app.state.settings = settings
    app.state.game = game
    app.state.fights = FightChannels()
    app.state.tokens = Tokens(settings.session_secret)
    app.state.mailer = mailer or default_mailer(settings)
    app.add_middleware(
        TolerantSessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="terraforma_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.include_router(account_router)
    app.include_router(twofa_router)
    app.include_router(heroes_router)
    app.include_router(inventory_router)
    app.include_router(trading_router)
    app.include_router(relations_router)
    app.include_router(towns_router)
    app.include_router(alliances_router)
    app.include_router(ratings_router)
    app.include_router(ballots_router)
    app.include_router(profiles_router)

    @app.get("/api/about")
    async def about(request: Request) -> dict:
        game = request.app.state.game
        return {"engine": ENGINE.strip(), "game": game.name if game else None}

    @app.get("/api/health")
    async def health(request: Request) -> JSONResponse:
        """For the container's health check: up, and the database answers."""
        try:
            async with request.app.state.sessionmaker() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False, "database": False}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        return JSONResponse({"ok": True, "database": True})

    app.include_router(fights_router)
    app.add_api_websocket_route("/ws/fights/{fight_id}", fight_socket)

    return app
