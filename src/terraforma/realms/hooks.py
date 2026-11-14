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

A settlement that lives between realms (a wayhouse) is judged by the realms around it, not by its own: ``governs`` says so, and ``support`` says how
much the realms within its sphere give it (``Influence``); it falls when the support drops under ``holds_at`` and is founded when it reaches ``founds_at``.
"""

import random
from dataclasses import dataclass

from .models import Realm, Settlement


@dataclass(frozen=True)
class Influence:
    """What one realm (a faction) has in a settlement's sphere: ``realm``, how many of its live settlements stand within the sphere (``towns``), the
    distance in tiles to the nearest (``nearest``, 0 to the sphere), and how full the supplies of all of them are on average, in whole percent of
    their capacity (``fullness``; 0 if they keep none). The settlement itself is not counted."""

    realm: Realm
    towns: int
    nearest: int
    fullness: int


@dataclass(frozen=True)
class Raid:
    """A raid the game says has fallen on a live settlement: for $ticks ticks of the world clock the settlement's own sources of $material are cut by $cut
    percent (``realms.supply.set_cut``), and $text, if any, is posted to the bulletin (``realms.news``)."""

    settlement: Settlement
    material: str
    cut: int
    ticks: int
    text: str = ""


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
        settlement per realm is founded in a pass, dormant ones before ruins, and one the world refuses to found is skipped."""
        return False

    #: How far, in tiles, a settlement's sphere reaches when its row (``Settlement.sphere``, the seed's ``sphere``) does not say.
    sphere_default: int = 12
    #: A governed settlement that is live falls when its support is under this, and one that is dormant or in ruins is founded when it is at or over ``founds_at``
    #: (a gap between the two keeps it from opening and closing at every pass).
    holds_at: int = 30
    founds_at: int = 60

    def raids(self, realm: Realm, settlements: list[Settlement], rng: random.Random, elapsed: int) -> list[Raid]:
        """The raids that fall on the realm's live $settlements in the $elapsed ticks since it was last judged, drawn with $rng (a repeatable stream for this
        realm and this moment: use it, never ``random``). None, by default. A raid on a supply that is under a raid already starts over."""
        return []

    def governs(self, settlement: Settlement) -> bool:
        """Whether the settlement is judged by the realms around it (``support``) and not by the strength of its own realm (``falls`` and ``founds``).
        Never, by default."""
        return False

    def support(self, settlement: Settlement, influences: list[Influence]) -> int:
        """How much the realms in the settlement's sphere give it a reason to exist, as a whole number the game scales to ``holds_at`` and ``founds_at``.
        $influences has one entry for each realm with a live settlement within the sphere (none when there are none). 0, by default."""
        return 0
