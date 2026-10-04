"""The example game: the smallest game that uses everything the engine gives a game, with no content of its own.

It is the engine's development game (``python -m terraforma serve`` runs it), the game the browser tests play, and a model
for a game of your own: a seed, assets, and browser code, handed to the engine as a ``Game``. Its pictures are
drawn for this repository (see ``assets/ASSETS.md``).
"""

from pathlib import Path

from ..game import Game

HERE = Path(__file__).parent

GAME = Game(
    name="TerraForma Example",
    seed_dir=HERE / "seed",
    assets_dir=HERE / "assets",
    client_dir=HERE / "client",
    client_modules=("example.js",),
    client_styles=("example.css",),
)
