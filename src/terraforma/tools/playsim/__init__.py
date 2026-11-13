"""Simulated play tests for the living world: realm strength, town supply, supply quests, falls and foundings.

Not unit tests. They start a game's real app, then play it: simulated players sign up and shop over HTTP, an admin stirs the world, the
world clock runs on and housekeeping runs as the server runs it, and each scenario ends in pass/fail checks and a story of what changed.

    python -m terraforma.tools.playsim                       # every scenario, on the demo world
    python -m terraforma.tools.playsim --list
    python -m terraforma.tools.playsim -s supply-quest -s shop-prices
    python -m terraforma.tools.playsim --game my_game:GAME --shop smith --item sword    # a game's own world and rules
"""
