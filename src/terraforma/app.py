"""The web app: the engine's server calls and the fight WebSocket.

Each call is its own route with a strict Pydantic model for its
arguments, so a call with the wrong shape is refused before any game code
runs. The account calls are in accounts/routes.py.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Path, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from . import __doc__ as ENGINE
from . import logscrub
from .accounts.routes import router as account_router
from .accounts.twofa_routes import router as twofa_router
from .accounts.tokens import Tokens
from .api.deps import ActingAccount
from .api.security import SESSION_ACCOUNT, SESSION_VERSION, same_origin
from .db.session import make_engine, make_sessionmaker
from .fights.channels import FightChannels
from .content.loader import load_content
from .game import Game
from .heroes.routes import router as heroes_router
from .keys import KeyRing
from .mail import Mailer, OutboxMailer, SmtpMailer
from .models import Account
from .seed import load_seed
from .settings import Settings


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class FighterRef(Strict):
    """A fighter's place in a fight: party, group, and place in the group."""

    party: int = Field(ge=0, le=7)
    group: int = Field(ge=0, le=3)
    character: int = Field(ge=0, le=9)


class FightCommand(Strict):
    fighter: FighterRef
    command: Literal[
        "attack_left", "attack_right", "item", "equip", "skill", "spell", "defend", "flee", "equip_weapon"
    ]
    # Which item, skill or spell, by its place in the fighter's list.
    using: int = Field(default=0, ge=0, le=255)
    target: FighterRef


FightId = Annotated[int, Path(ge=1)]


def default_mailer(settings: Settings) -> Mailer:
    if settings.mail is not None:
        return SmtpMailer(settings.mail)
    return OutboxMailer(settings.outbox_dir)


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
                await load_content(session, load_seed(game.seed_dir))
        yield
        await engine.dispose()

    app = FastAPI(title=game.name if game else "TerraForma", lifespan=lifespan)
    app.state.settings = settings
    app.state.game = game
    app.state.fights = FightChannels()
    app.state.tokens = Tokens(settings.session_secret)
    app.state.mailer = mailer or default_mailer(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="terraforma_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.include_router(account_router)
    app.include_router(twofa_router)
    app.include_router(heroes_router)

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

    @app.post("/api/fights/{fight_id}/commands", status_code=status.HTTP_202_ACCEPTED)
    async def fight_command(fight_id: FightId, body: FightCommand, request: Request, account: ActingAccount) -> dict:
        # The fight engine arrives in phase 6: for now the command is
        # checked for shape and pushed to everyone watching the fight.
        await request.app.state.fights.push(
            fight_id, {"type": "command", "fight": fight_id, "by": account.id, **body.model_dump()}
        )
        return {"accepted": True}

    @app.websocket("/ws/fights/{fight_id}")
    async def fight_socket(socket: WebSocket, fight_id: FightId) -> None:
        if not same_origin(socket) or not await logged_in(socket):
            await socket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        channels: FightChannels = socket.app.state.fights
        await socket.accept()
        channels.join(fight_id, socket)
        try:
            await socket.send_json({"type": "joined", "fight": fight_id, "watching": channels.watching(fight_id)})
            while True:
                # The page only listens; anything it sends is ignored (commands go through the calls).
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            channels.leave(fight_id, socket)

    return app


async def logged_in(socket: WebSocket) -> bool:
    account_id = socket.session.get(SESSION_ACCOUNT)
    if account_id is None:
        return False
    async with socket.app.state.sessionmaker() as session:
        account = await session.get(Account, account_id)
    return account is not None and account.session_version == socket.session.get(SESSION_VERSION)
