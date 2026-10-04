"""The engine on its own, for development: the example game and the engine's calls.

    python -m terraforma serve          (in the container: the engine's compose.yml app service)
"""

from .app import create_app
from .example import GAME
from .settings import load_settings


def app():
    return create_app(load_settings(), GAME)
