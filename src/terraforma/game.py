"""What a game hands the engine. The engine never imports a game; a game builds the app with itself.

    from terraforma.app import create_app
    from terraforma.game import Game

    game = Game(name="Vanguard Tavern")
    app = create_app(settings, game)
"""

from dataclasses import dataclass, field
from pathlib import Path

from .economy import Economy, TeamGold
from .fights.rules import Rules


@dataclass(frozen=True)
class Game:
    name: str
    # The game's seed data (JSON) and assets, when it has them.
    seed_dir: Path | None = None
    assets_dir: Path | None = None
    # How fights are played: the stats, the resources and every formula. Override Rules to change them.
    rules: Rules = field(default_factory=Rules)
    # Where gold lives and how it moves (on the team, DragonStar's way, unless the game says otherwise).
    economy: Economy = field(default_factory=TeamGold)
