"""The browser client the engine serves: its pages and files, what a game adds to it, and the CSRF token a reload needs.
(The pages running in a real browser are tests/e2e.)"""

import re

import pytest

from terraforma.client import ClientError, check_game_client
from terraforma.example import GAME as EXAMPLE
from terraforma.game import Game

from .helpers import expect


@pytest.fixture
def game():
    return EXAMPLE


def test_the_pages_are_served_with_the_security_headers(app_client):
    for path in ("/", "/play/", "/account/", "/team/1", "/confirm-email", "/change-email", "/reset-password", "/confirm-2fa"):
        page = expect(app_client.get(path), 200)
        assert page.headers["content-type"].startswith("text/html")
        assert "script-src" not in page.headers["content-security-policy"], "default-src 'self' covers scripts: nothing inline, nothing foreign"
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["x-content-type-options"] == "nosniff"
        assert "<script>" not in page.text, "no inline script: the policy would refuse it"


def test_every_address_the_engine_mails_opens_an_account_page(app_client, mailbox):
    """The links in the emails (accounts.routes, accounts.twofa_routes) must land on a page, with the token in the query."""
    from .test_accounts import MIKE, log_in, register, token_in

    register(app_client)
    sent = mailbox.last_to(MIKE["email"])
    path = re.search(r"https?://[^/\s]+(/[a-z0-9-]+)\?token=", sent.body).group(1)
    assert 'id="app"' in expect(app_client.get(f"{path}?token={token_in(sent)}"), 200).text


def test_the_account_and_team_page_scripts_are_served(app_client):
    for name in ("account", "forms", "register", "settings", "tokens", "team", "team-editor"):
        expect(app_client.get(f"/client/js/{name}.js"), 200)


def test_the_engines_code_is_served_as_modules_and_styles(app_client):
    script = expect(app_client.get("/client/js/shell.js"), 200)
    assert "javascript" in script.headers["content-type"]
    assert "css" in expect(app_client.get("/client/css/engine.css"), 200).headers["content-type"]
    expect(app_client.get("/client/js/nothing.js"), 404)


def test_the_art_loader_is_served(app_client):
    expect(app_client.get("/client/js/art.js"), 200)


def test_the_play_page_says_to_turn_the_device_and_the_stylesheet_shows_it_in_portrait(app_client):
    assert 'id="rotate"' in app_client.get("/play/").text
    assert "(orientation: portrait)" in app_client.get("/client/css/engine.css").text


def test_the_manifest_lists_what_the_game_adds(app_client):
    answer = expect(app_client.get("/api/client"), 200).json()
    assert answer["game"] == "TerraForma Example"
    assert answer["modules"] == ["/game/example.js"]
    assert answer["styles"] == ["/game/example.css"]
    assert answer["assets"] == "/assets/"
    expect(app_client.get("/game/example.js"), 200)
    assert expect(app_client.get("/assets/grass.svg"), 200).headers["content-type"].startswith("image/svg")


def test_a_game_cannot_serve_files_outside_its_folders(app_client):
    for path in ("/assets/../seed/maps.json", "/assets/%2e%2e/seed/maps.json", "/game/..%2fexample.py", "/client/../__init__.py"):
        assert app_client.get(path).status_code in (400, 404), path


def test_the_example_game_is_valid_and_its_tileset_is_drawn_by_its_assets(app_client):
    import json

    for tile in json.loads((EXAMPLE.seed_dir / "maps.json").read_text())[0]["tileset"]:
        expect(app_client.get(f"/assets/{tile['art']}"), 200)


@pytest.mark.parametrize("game", [None], ids=["no game"])
def test_an_app_without_a_game_still_serves_the_engines_client(app_client):
    assert expect(app_client.get("/api/client"), 200).json() == {
        "engine": expect(app_client.get("/api/about"), 200).json()["engine"], "game": None, "modules": [], "styles": [], "assets": None,
    }
    expect(app_client.get("/play/"), 200)
    expect(app_client.get("/assets/grass.svg"), 404)
    expect(app_client.get("/game/example.js"), 404)


def test_the_session_call_says_who_is_logged_in_and_gives_a_reload_its_csrf_token(app_client, mailbox):
    from .test_accounts import MIKE, log_in, register, token_in

    register(app_client)
    app_client.post("/api/confirm-email", json={"token": token_in(mailbox.last_to(MIKE["email"]))})
    assert expect(app_client.get("/api/session"), 200).json() == {"account": None, "csrf_token": None}, "no login is not an error"
    logged_in = expect(log_in(app_client), 200).json()
    assert expect(app_client.get("/api/session"), 200).json() == {
        "account": {"username": "Mike", "handle": None}, "csrf_token": logged_in["csrf_token"],
    }
    expect(app_client.post("/api/logout", headers={"X-CSRF-Token": logged_in["csrf_token"]}), 200)
    assert expect(app_client.get("/api/session"), 200).json() == {"account": None, "csrf_token": None}


# --- what a game may say about its client (a mistake stops the server at start) ----------------------------------

def test_a_games_client_files_are_checked(tmp_path):
    (tmp_path / "game.js").write_text("export default () => {}\n")
    (tmp_path / "game.css").write_text("\n")
    check_game_client(Game(name="G", client_dir=tmp_path, client_modules=("game.js",), client_styles=("game.css",)))

    wrong = {
        "no folder": Game(name="G", client_modules=("game.js",)),
        "a file that isn't there": Game(name="G", client_dir=tmp_path, client_modules=("missing.js",)),
        "a path out of the folder": Game(name="G", client_dir=tmp_path, client_modules=("../game.js",)),
        "an absolute path": Game(name="G", client_dir=tmp_path, client_modules=("/etc/passwd",)),
        "a module that isn't JavaScript": Game(name="G", client_dir=tmp_path, client_modules=("game.css",)),
        "a style that isn't CSS": Game(name="G", client_dir=tmp_path, client_styles=("game.js",)),
        "a folder that isn't one": Game(name="G", client_dir=tmp_path / "nothing"),
        "assets that aren't a folder": Game(name="G", assets_dir=tmp_path / "game.js"),
    }
    for what, game in wrong.items():
        with pytest.raises(ClientError):
            check_game_client(game)
            pytest.fail(f"accepted {what}")
