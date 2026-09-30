"""What a game hands the engine. The engine never imports a game; a game builds the app with itself.

    from terraforma.app import create_app
    from terraforma.game import Game

    game = Game(name="Vanguard Tavern")
    app = create_app(settings, game)
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Game:
    name: str
    # The game's seed data (JSON) and assets, when it has them.
    seed_dir: Path | None = None
    assets_dir: Path | None = None
