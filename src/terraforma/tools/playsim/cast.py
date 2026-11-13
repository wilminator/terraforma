"""Who plays in a scenario: which realm, town and material it stirs, found in the world a game gave us instead of being named in the script.

A scenario written against these works for any game that has realms, settlements and supplies; a game with no shop to watch, no dormant
village or no hamlet simply has those parts of the cast left empty, and the scenarios that need them say they were skipped.
"""

from dataclasses import dataclass, field

from ...content.models import Item
from ...market.hooks import Supplied
from ...realms.models import Material, Realm, Settlement


@dataclass
class Cast:
    realm: str = ""        # a kingdom with live towns that keep supplies
    town: str = ""         # one of its live towns that keeps something to run low
    material: str = ""     # the material it keeps (one that fills when left alone, if it has one)
    hamlet: str = ""       # its smallest live town, if the realm has more than one live town
    dormant: str = ""      # one of its dormant villages, if any
    shop: str = ""         # a shop whose item depends on a supply, if the game has one
    item: str = ""
    shop_town: str = ""    # that supply's settlement and material
    shop_material: str = ""
    notes: list[str] = field(default_factory=list)


def transient_realm(view: dict) -> Realm:
    """A Realm to ask the game's hooks about (never stored)."""
    return Realm(id=view["id"], key=view["key"], name=view["name"], kind=view["kind"], strength=view["strength"])


def transient_town(view: dict) -> Settlement:
    return Settlement(id=view["id"], key=view["key"], name=view["name"], realm_id=view["realm_id"], map_id=view["map_id"], state=view["state"],
                      tier=view["tier"], x=view["x"], y=view["y"])


def transient_material(view: dict) -> Material:
    return Material(key=view["material"], name=view["name"])


def discover(sim, shop: str = "", item: str = "") -> Cast:
    """Picks the cast from the world as it stands: the kingdom with the most live towns, its richest-running town, its smallest town, a
    dormant village of it, and (when the game names a shop and item, or the market ties one) the shop to watch."""
    world = sim.world()
    cast = Cast()
    live = [each for each in world["settlements"] if each["state"] == "live"]
    kingdoms = [each for each in world["realms"] if each["kind"] == "kingdom"] or world["realms"]
    best = max(kingdoms, key=lambda realm: sum(1 for each in live if each["realm_id"] == realm["id"]), default=None)
    if best is None:
        cast.notes.append("the world has no realms")
        return cast
    cast.realm = best["key"]
    towns = sorted((each for each in live if each["realm_id"] == best["id"]), key=lambda each: (each["tier"], each["key"]))
    keeping = [(each, sim.supplies(each["key"])) for each in towns]
    keeping = [(town, rows) for town, rows in keeping if rows]
    if keeping:
        town, rows = max(keeping, key=lambda pair: (max(row["rate"] - row["drain"] for row in pair[1]), pair[0]["tier"]))
        row = max(rows, key=lambda each: (each["rate"] > each["drain"], each["drain"] > 0, each["rate"] - each["drain"]))
        cast.town, cast.material = town["key"], row["material"]
    else:
        cast.notes.append(f"no live town of {best['key']} keeps any supply")
    if len(towns) > 1:
        cast.hamlet = towns[0]["key"]
    dormant = [each for each in world["settlements"] if each["state"] == "dormant" and each["realm_id"] == best["id"]]
    cast.dormant = dormant[0]["key"] if dormant else ""
    if shop and item:
        cast.shop, cast.item = shop, item
        tied = sim.run(lambda db: _tied(db, sim.game.market, shop, item))
        if tied is not None:
            cast.shop_town, cast.shop_material = tied.settlement, tied.material
        else:
            cast.notes.append(f"the market does not tie {item} at {shop} to a supply")
    return cast


async def _tied(db, market, shop, item_key) -> Supplied | None:
    from sqlalchemy import select

    item = await db.scalar(select(Item).where(Item.key == item_key))
    return None if item is None else await market.supplied(db, shop, item)
