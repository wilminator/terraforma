"""The world's randomness, from one seed.

Everything random in the game (map generation, loot, AI choices, combat
rolls) draws from a named stream of its world's seed, never from the
global ``random`` module. The same seed and the same stream names give
the same results, so a generated world can be rebuilt and a fight bug
replayed exactly.

    rng = WorldRng(world.seed)
    rolls = rng.stream("fight", fight.id, round_number)
    damage = rolls.randint(1, 6)

Not for secrets: tokens and passwords use the ``secrets`` module.
"""

import hashlib
import random


class WorldRng:
    def __init__(self, seed: int):
        self.seed = seed

    def stream(self, *name: object) -> random.Random:
        """An independent, repeatable random stream for $name (strings and numbers)."""
        label = "\x1f".join(str(part) for part in (self.seed, *name))
        digest = hashlib.sha256(label.encode()).digest()
        return random.Random(int.from_bytes(digest[:16], "big"))
