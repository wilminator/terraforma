"""The example game: the smallest game that uses everything the engine gives a game, with no content of its own.

It is the engine's development game (``python -m terraforma serve`` runs it), the game the browser tests play, and a model
for a game of your own: a seed, assets, and browser code, handed to the engine as a ``Game``. Its pictures are
drawn for this repository (see ``assets/ASSETS.md``).
"""

from pathlib import Path

from ..fights.rules import Rules
from ..game import Game
from ..heroes.hooks import Roster
from ..models import Map
from ..towns.hooks import Towns
from ..world.start import HUB_NAME

HERE = Path(__file__).parent


class ExampleRules(Rules):
    """DragonStar's rules, with its teams: up to three of them, each with one to four heroes."""

    team_min = 1
    team_max = 4
    max_teams = 3


class ExampleRoster(Roster):
    """A hero may be removed from a team (or replaced), and the empty place filled later. Moving them to another team stays refused."""

    async def may_remove(self, session, account, hero):
        return None


class ExampleTowns(Towns):
    """The town is the gate tile where heroes start: a party that steps onto it is suspended there until its teams are ready to leave.
    The rest of the meadow is open ground to walk."""

    async def is_town(self, session, map_id, x, y):
        game_map = await session.get(Map, map_id)
        return game_map is not None and game_map.name == HUB_NAME and (x, y) == (0, 0)


GAME = Game(
    name="TerraForma Example",
    rules=ExampleRules(),
    roster=ExampleRoster(),
    towns=ExampleTowns(),
    seed_dir=HERE / "seed",
    assets_dir=HERE / "assets",
    client_dir=HERE / "client",
    client_modules=("example.js",),
    client_styles=("example.css",),
)
