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
from .alliances.hooks import Alliances
from .relations.hooks import Relations
from .pvp.hooks import PvpZones
from .market.hooks import Market
from .npcs.hooks import Npcs
from .npcs.inn import Inn
from .towns.hooks import Towns


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
    # How teams feel about each other: the scale's bands, who may form a relationship, and every change to one.
    relations: Relations = field(default_factory=Relations)
    # What roles an alliance of teams has, what each may do, and who may found or join one.
    alliances: Alliances = field(default_factory=Alliances)
    # Which places are towns (where a party comes apart into its teams and is put back together), and how its teams group.
    towns: Towns = field(default_factory=Towns)
    # Where a party may pick a fight with another party (nowhere, by default); see ``Rules.may_start_pvp`` for the range window.
    pvp: PvpZones = field(default_factory=PvpZones)
    # Who may talk to an NPC from where they stand, and what a game does with the dialog tags the engine leaves to it (heal, a shop...).
    npcs: Npcs = field(default_factory=Npcs)
    market: Market = field(default_factory=Market)
    inn: Inn = field(default_factory=Inn)
