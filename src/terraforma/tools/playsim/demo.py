"""The demo world: the example game with a living world of its own, for scenarios that need no particular game.

Two kingdoms and a free-towns league, each with towns that keep iron, hide and herbs; a dormant village waits for Ardent to grow. The
hooks are small and the numbers round, so the arithmetic in a scenario can be followed by eye:

* a realm's strength follows how full its towns' supplies are, 2 a tick; a hamlet falls under 15, a dormant village or ruin wakes from 60;
* a supply below 25% asks for a quest (live at once, 100 pay per 10 goods handed in), hand-ins fill it back;
* the general shop sells herbs, which the Mill's herbs supply decides (dearer as they run low, sold out below 10%).
"""

import dataclasses

from ...content.schema import MaterialSeed, RealmSeed, SettlementSeed, SupplySeed
from ...example import GAME
from ...market.hooks import Market, Supplied
from ...quests.hooks import Needs
from ...realms import service
from ...realms.hooks import Realms

SHOP, SHOP_ITEM = "general", "herb"


class DemoRealms(Realms):
    step = 2

    def target(self, realm, fullness):
        return sum(fullness) // len(fullness) if fullness else None

    def falls(self, realm, settlement):
        return realm.strength < 15 and settlement.tier == 0

    def founds(self, realm, settlement):
        return realm.strength >= 60


class DemoNeeds(Needs):
    def supply_floor(self, settlement, material):
        return 25

    def supply_slot(self, settlement, material):
        return "supply"

    def needs_approval(self, band):
        return False

    def supply_reward(self, rng, settlement, material, amount):
        return {"gold": amount * 10}


class DemoMarket(Market):
    sold_out_below = 10

    async def level(self, session, rules, shop, hero, party):
        return 1000

    async def supplied(self, session, shop, item):
        return Supplied("mill", "herbs") if item.key == SHOP_ITEM else None


def _supplies(**each):
    return [SupplySeed(material=key, capacity=100, rate=values[0], drain=values[1], level=values[2]) for key, values in each.items()]


MATERIALS = [MaterialSeed(key="iron", name="Iron"), MaterialSeed(key="hide", name="Hide"), MaterialSeed(key="herbs", name="Herbs")]
REALMS = [RealmSeed(key="ardent", name="Ardent", strength=50), RealmSeed(key="boreal", name="Boreal", strength=50),
          RealmSeed(key="league", name="Free League", strength=50, kind="minor")]
TOWNS = [
    SettlementSeed(key="capital", name="Ardent City", realm="ardent", map="hub", tier=3, x=1, y=1, supplies=_supplies(iron=(6, 3, 80), hide=(4, 2, 80))),
    SettlementSeed(key="ironford", name="Ironford", realm="ardent", map="hub", tier=1, x=2, y=1, supplies=_supplies(iron=(8, 4, 80))),
    SettlementSeed(key="mill", name="Mill Hamlet", realm="ardent", map="hub", tier=0, x=3, y=1, supplies=_supplies(herbs=(4, 2, 80), hide=(2, 1, 60))),
    SettlementSeed(key="newhaven", name="Newhaven", realm="ardent", map="hub", tier=0, x=4, y=1, state="dormant", supplies=_supplies(herbs=(4, 2, 50))),
    SettlementSeed(key="frostkeep", name="Frostkeep", realm="boreal", map="hub", tier=3, x=5, y=1, supplies=_supplies(iron=(5, 3, 80), hide=(5, 3, 80))),
    SettlementSeed(key="pinewick", name="Pinewick", realm="boreal", map="hub", tier=0, x=6, y=1, supplies=_supplies(hide=(3, 2, 70))),
    SettlementSeed(key="crossing", name="The Crossing", realm="league", map="hub", tier=0, x=7, y=1, supplies=_supplies(herbs=(3, 2, 70))),
]

#: The game the demo runs: the example game with the demo hooks.
DEMO_GAME = dataclasses.replace(GAME, name="TerraForma Demo", realms=DemoRealms(), needs=DemoNeeds(), market=DemoMarket())


def stage(sim) -> None:
    """Builds the demo world in an empty database (the example game has no realms of its own)."""
    sim.run(lambda db: service.apply_realms(db, sim.world_id, REALMS, TOWNS, MATERIALS))
