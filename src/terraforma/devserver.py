"""The engine on its own, for development: no game content, just the engine's calls.

    python -m terraforma serve          (in the container: the engine's compose.yml app service)
"""

from .app import create_app
from .game import Game
from .settings import load_settings


def app():
    return create_app(load_settings(), Game(name="TerraForma (development)"))
