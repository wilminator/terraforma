"""The web app: the engine's server calls and the fight WebSocket.

Each call is its own route with a strict Pydantic model for its
arguments, so a call with the wrong shape is refused before any game code
runs. The account calls are in accounts/routes.py.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx2 as httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __doc__ as ENGINE
from . import logscrub
from .accounts.external_routes import router as external_router
from .accounts.routes import router as account_router
from .accounts.twofa_routes import admin_router, router as twofa_router
from .accounts.tokens import Tokens
from .api.security import TolerantSessionMiddleware
from .db.session import make_engine, make_sessionmaker
from .fights import live
from .fights.channels import FightChannels
from .fights.routes import economy_of, fight_socket, relations_of, rules_of
from .fights.pending_routes import router as pending_drops_router
from .fights.routes import router as fights_router
from .fights.timing_routes import router as timing_router
from .animations import check_hero_back_views
from .content.loader import load_content
from .game import Game
from .heroes.inventory_routes import router as inventory_router
from .storage.routes import router as storage_router
from .heroes.routes import router as heroes_router
from .alliances.ballot_routes import router as ballots_router
from .alliances.routes import router as alliances_router
from .challenge.routes import router as challenge_router
from .storegrant.routes import router as storegrant_router
from .guild.routes import router as guild_router
from .standing.routes import router as standing_router
from .maps.routes import router as maps_router
from .monsterplay.routes import fight_router as monster_fight_router
from .monsterplay.routes import guest_fight_socket
from .monsterplay.routes import guest_router as monster_guest_router
from .monsterplay.routes import router as monsters_router
from .oidc.routes import router as oidc_router
from .market.routes import router as market_router
from .npcs.routes import router as npcs_router
from .reach.routes import router as reach_router
from .parties.routes import router as parties_router
from .playtime.routes import router as playtime_router
from .fighttags.routes import router as fighttags_router
from .fightpages.routes import router as fightpages_router
from .profiles.routes import router as profiles_router
from .social.routes import router as social_router
from .quests.manual_routes import router as manual_quests_router
from .content.routes import router as content_router
from .gamestate.routes import router as gamestate_router
from .gamestate import service as gamestate
from .gamestate.document import sections_by_name
from .quests.manual_routes import router as manual_quests_router
from .quests.needs_routes import router as needs_router
from .quests.rotation_routes import router as rotation_router
from .quests.weekly_routes import router as weekly_quests_router
from .relations.rating_routes import router as ratings_router
from .relations.routes import router as relations_router
from .chat.routes import router as chat_router
from .voice.routes import router as voice_router
from .pvp.routes import router as pvp_router
from .towns.routes import router as towns_router
from .trading.routes import router as trading_router
from . import client, housekeeping
from .keys import KeyRing
from .mail import Mailer, OutboxMailer, SmtpMailer
from .addons import integrate
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
        game = app.state.game
        await housekeeping.run(app.state.sessionmaker, app.state.settings, game.rules if game else None, game.needs if game else None)


def create_app(settings: Settings, game: Game | None = None, *, mailer: Mailer | None = None) -> FastAPI:
    logscrub.install()
    integrated = integrate(game, settings.addons)  # a listed add-on the game lacks, or a clash between layers, stops the server here

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # No key, no start: better than running on and failing at the first 2FA setup.
        app.state.keys = KeyRing.load(settings.key_dir)
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        if game and integrated.seed_dirs:
            # A seed that doesn't check out stops the server here, naming what's wrong.
            async with app.state.sessionmaker() as session, session.begin():
                layered = integrated.load_seed()
                await load_content(session, layered.rows, game.rules.stats, game.rules.resource_names, game.rules.drop_chance_scale, layered.owners)
                if integrated.asset_dirs:
                    check_hero_back_views(layered.rows, integrated.asset_dirs)
        # The first, empty settings snapshot, if the database has none yet.
        async with app.state.sessionmaker() as session, session.begin():
            await gamestate.ensure(session, app.state.state_sections)
        timer = asyncio.create_task(fight_timer(app, settings.fight_timer_seconds)) if settings.fight_timer_seconds else None
        tidier = asyncio.create_task(housekeeping_timer(app, settings.housekeeping_seconds)) if settings.housekeeping_seconds else None
        yield
        for task in (timer, tidier):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await engine.dispose()
        await http_client.aclose()

    http_client = httpx.AsyncClient()  # for the calls to sign-in providers (tests replace app.state.http_client)
    app = FastAPI(title=game.name if game else "TerraForma", lifespan=lifespan)
    app.state.settings = settings
    app.state.game = game
    app.state.state_sections = sections_by_name(game.state_sections if game else ())
    app.state.addons = integrated.addons
    app.state.asset_dirs = integrated.asset_dirs
    app.state.fights = FightChannels()
    app.state.http_client = http_client
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
    app.include_router(admin_router)
    app.include_router(external_router)
    app.include_router(heroes_router)
    app.include_router(inventory_router)
    app.include_router(storage_router)
    app.include_router(trading_router)
    app.include_router(pending_drops_router)
    app.include_router(relations_router)
    app.include_router(chat_router)
    app.include_router(voice_router)
    app.include_router(towns_router)
    app.include_router(pvp_router)
    app.include_router(alliances_router)
    app.include_router(ratings_router)
    app.include_router(ballots_router)
    app.include_router(timing_router)
    app.include_router(profiles_router)
    app.include_router(playtime_router)
    app.include_router(social_router)
    app.include_router(weekly_quests_router)
    app.include_router(rotation_router)
    app.include_router(needs_router)
    app.include_router(manual_quests_router)
    app.include_router(content_router)
    app.include_router(gamestate_router)
    app.include_router(manual_quests_router)
    app.include_router(fighttags_router)
    app.include_router(fightpages_router)
    app.include_router(challenge_router)
    app.include_router(storegrant_router)
    app.include_router(npcs_router)
    app.include_router(reach_router)
    app.include_router(market_router)
    app.include_router(guild_router)
    app.include_router(standing_router)
    app.include_router(parties_router)
    app.include_router(maps_router)
    app.include_router(monsters_router)
    app.include_router(monster_guest_router)
    app.include_router(oidc_router)
    app.include_router(monster_fight_router)

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
    app.add_api_websocket_route("/ws/monsters/guest/fights/{fight_id}", guest_fight_socket)
    client.install(app, game, integrated.asset_dirs)  # last: the calls above are matched first

    return app
