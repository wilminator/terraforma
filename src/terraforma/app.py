"""The web app: the engine's server calls and the fight WebSocket.

Proof of concept: login, one typed call (a fight command) and the fight
socket it pushes to. Each call is its own route with a Pydantic model for
its arguments, so a call with the wrong shape is refused before any game
code runs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from . import __doc__ as ENGINE
from .accounts.service import authenticate
from .api.security import SESSION_ACCOUNT, acting_account_id, current_account_id, same_origin, start_session
from .db.session import make_engine, make_sessionmaker
from .fights.channels import FightChannels
from .game import Game
from .models import Account
from .settings import Settings


# --- the calls' arguments -------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class LoginRequest(Strict):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=1024)


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


# --- the app ---------------------------------------------------------------

def create_app(settings: Settings, game: Game | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title=game.name if game else "TerraFroma", lifespan=lifespan)
    app.state.game = game
    app.state.fights = FightChannels()
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="terraforma_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    async def database(request: Request) -> AsyncIterator[AsyncSession]:
        async with request.app.state.sessionmaker() as session:
            async with session.begin():
                yield session

    Db = Annotated[AsyncSession, Depends(database)]

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

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, db: Db) -> dict:
        account = await authenticate(db, body.username, body.password)
        if account is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
        token = start_session(request, account.id)
        return {"username": account.username, "csrf_token": token}

    @app.post("/api/logout")
    async def logout(request: Request, account_id: Annotated[int, Depends(acting_account_id)]) -> dict:
        request.session.clear()
        return {"ok": True}

    @app.get("/api/me")
    async def me(db: Db, account_id: Annotated[int, Depends(current_account_id)]) -> dict:
        account = await db.get(Account, account_id)
        if account is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
        return {"username": account.username}

    @app.post("/api/fights/{fight_id}/commands", status_code=status.HTTP_202_ACCEPTED)
    async def fight_command(
        fight_id: FightId,
        body: FightCommand,
        request: Request,
        account_id: Annotated[int, Depends(acting_account_id)],
    ) -> dict:
        # The fight engine arrives in phase 6: for now the command is
        # checked for shape and pushed to everyone watching the fight.
        await request.app.state.fights.push(
            fight_id, {"type": "command", "fight": fight_id, "by": account_id, **body.model_dump()}
        )
        return {"accepted": True}

    @app.websocket("/ws/fights/{fight_id}")
    async def fight_socket(socket: WebSocket, fight_id: FightId) -> None:
        if not same_origin(socket) or socket.session.get(SESSION_ACCOUNT) is None:
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
