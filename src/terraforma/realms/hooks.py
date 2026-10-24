"""What a game decides about its realms' strength, and when a settlement falls or is founded. A game subclasses ``Realms`` and hands it to
``Game(realms=...)``. By default nothing moves: strength stays where the seed and the admin put it, and no settlement falls or is founded.
This is a public interface (the license exception covers it): the method names and signatures are what games build on.

    class Kingdoms(terraforma.realms.hooks.Realms):
        step = 2  # a realm's strength moves by at most 2 each tick of the world clock

        def target(self, realm, fullness):
            return sum(fullness) // len(fullness) if fullness else None  # follow how full its towns' supplies are

        def falls(self, realm, settlement):
            return realm.strength < 10 and settlement.tier == 0  # a hamlet of a failing realm falls

        def founds(self, realm, settlement):
            return realm.strength >= 60  # a strong realm wakes a dormant village or raises a fallen one
"""

from .models import Realm, Settlement


class Realms:
    #: The most a realm's strength moves toward its target in one tick of the world clock.
    step: int = 1

    def target(self, realm: Realm, fullness: list[int]) -> int | None:
        """The strength the realm drifts toward, given $fullness (how full each supply of each of its live settlements is, in whole percent
        of its capacity; empty when it has none). None, the default, means it does not drift."""
        return None

    def falls(self, realm: Realm, settlement: Settlement) -> bool:
        """Whether a live settlement of the realm falls now, at the realm's current strength. Never, by default."""
        return False

    def founds(self, realm: Realm, settlement: Settlement) -> bool:
        """Whether a dormant settlement of the realm is founded, or one in ruins is founded again, now. Never, by default. At most one
        settlement per realm is founded in a pass, dormant ones before ruins, and one that can't be founded (for example, its map has no room) is skipped."""
        return False
