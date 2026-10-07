"""The browser client the engine serves: plain ES modules, no build step.

The engine's pages (``static/pages``) and code (``static/lib``) are generic: they draw what the server's calls
send and hold no game words. A game adds its own browser code through ``Game(client_dir=..., client_modules=...,
client_styles=...)``: the folder is served at ``/game/``, and the page loads the listed stylesheets and imports the
listed modules, in order, each calling its default export with the shell (see README, "The browser client").
The game's pictures and sounds (``Game(assets_dir=...)``) are served at ``/assets/``.

    /               the marketing site                  /client/...   the engine's own code and styles
    /play/          the game client (landscape)         /game/...     the game's client_dir
    /account/       the account pages                   /assets/...   the game's assets_dir
    /team/{id}      a team's own page (its heroes)
    /confirm-email  /change-email  /reset-password  /confirm-2fa     the pages the emailed links open (account pages too)
    GET /api/client what a page needs to know: the game's name, modules, styles and assets
"""

import re
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __doc__ as ENGINE
from ..game import Game

STATIC = Path(__file__).parent / "static"

#: A page runs only the engine's and the game's own files: nothing inline, nothing from another site.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}

CLIENT_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_./-]{0,127}$")


class ClientError(ValueError):
    """A game's client files are not as the game says: the server won't start on them."""


def check_game_client(game: Game) -> None:
    """Raises ClientError naming what is wrong with $game's browser code, so a typo stops the server at start, not a player's page."""
    listed = {"client_modules": (".js", ".mjs"), "client_styles": (".css",)}
    if (game.client_modules or game.client_styles) and game.client_dir is None:
        raise ClientError("client_modules and client_styles need client_dir")
    for field, suffixes in listed.items():
        for name in getattr(game, field):
            if not CLIENT_FILE.fullmatch(name) or ".." in name.split("/") or not name.endswith(suffixes):
                raise ClientError(f"{field}: {name!r} must be a plain file name under client_dir ending in {' or '.join(suffixes)}")
            if not (game.client_dir / name).is_file():
                raise ClientError(f"{field}: {name!r} is not in client_dir ({game.client_dir})")
    for field in ("client_dir", "assets_dir"):
        folder = getattr(game, field)
        if folder is not None and not Path(folder).is_dir():
            raise ClientError(f"{field}: {folder} is not a folder")


router = APIRouter()


def page(name: str) -> FileResponse:
    return FileResponse(STATIC / "pages" / name, media_type="text/html", headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"})


@router.get("/", include_in_schema=False)
async def site() -> FileResponse:
    return page("index.html")


@router.get("/play/", include_in_schema=False)
async def play() -> FileResponse:
    return page("play.html")


# The same page file answers the account pages and the pages the links in the engine's emails open (accounts.routes and
# accounts.twofa_routes build those addresses): it shows the card its address names.
@router.get("/account/", include_in_schema=False)
@router.get("/confirm-email", include_in_schema=False)
@router.get("/change-email", include_in_schema=False)
@router.get("/reset-password", include_in_schema=False)
@router.get("/confirm-2fa", include_in_schema=False)
async def account() -> FileResponse:
    return page("account.html")


@router.get("/team/{team_id}", include_in_schema=False)
async def team(team_id: int) -> FileResponse:
    """A team's own page (the browser asks the server for the team: only its player gets it)."""
    return page("team.html")


@router.get("/api/client")
async def manifest(request: Request, response: Response) -> dict:
    """What a page needs to start: the game's name and the files it adds (URLs, in the order to load them)."""
    game: Game | None = request.app.state.game
    response.headers["Cache-Control"] = "no-cache"
    return {
        "engine": ENGINE.strip(),
        "game": game.name if game else None,
        "modules": [f"/game/{name}" for name in game.client_modules] if game else [],
        "styles": [f"/game/{name}" for name in game.client_styles] if game else [],
        "assets": "/assets/" if game and game.assets_dir else None,
    }


def install(app: FastAPI, game: Game | None) -> None:
    """Adds the client's pages and files to $app. Mounted last: the calls come first."""
    if game is not None:
        check_game_client(game)
    app.include_router(router)
    app.mount("/client", StaticFiles(directory=STATIC / "lib"), name="client")
    if game and game.client_dir:
        app.mount("/game", StaticFiles(directory=game.client_dir), name="game-client")
    if game and game.assets_dir:
        app.mount("/assets", StaticFiles(directory=game.assets_dir), name="game-assets")
