"""Loading a game's checked content into the database. Safe to run on every start."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from ..maps.service import apply_maps
from ..npcs.service import apply_dialogs, apply_npcs
from ..realms.service import apply_realms
from ..quests.rotation import apply_slots
from ..quests.weekly import apply_templates
from ..world.start import ensure_start
from . import models
from .schema import DROP_SCALE, RESOURCES, STATS, Strict, check_seed, job_ability_key

# Parents before the rows that name them.
TABLES = {
    "statuses": models.Status,
    "abilities": models.Ability,
    "items": models.Item,
    "drop_tables": models.DropTable,
    "personalities": models.Personality,
    "jobs": models.Job,
    "monsters": models.Monster,
}


def _values(kind: str, row: Strict, active: bool = True) -> dict:
    """The row's columns: its fields, with nested formats as plain JSON."""
    values = row.model_dump(mode="json", by_alias=True)
    values.pop("section", None)  # (the seed's: it places an add-on's row in the admin's tree, and the tree is read from the seed)
    if kind == "personalities":
        values["animations"] = {
            name: values.pop(name)
            for name in ("base", "equip", "flee", "hit", "die", "attack_close", "attack_throw", "attack_shoot", "skill", "spell", "item")
        }
    if kind == "monsters":
        values["authored"] = False  # a seed row with an admin-authored monster's key takes it over
    return {**values, "active": active}


async def load_content(session: AsyncSession, seed: dict[str, list[dict]], stats: tuple[str, ...] | None = None,
                       resources: tuple[str, ...] | None = None, drop_scale: int | None = None,
                       owners: dict[str, dict[str, str | None]] | None = None) -> dict[str, int]:
    """Checks $seed (raising ContentError if it's wrong) and makes the database match it.

    New keys are added, known keys updated, and keys the seed no longer lists
    are marked inactive (never deleted). Returns how many rows of each kind
    the seed has. A seed with no content files leaves the database alone.

    $owners (``LayeredSeed.owners``) says which add-on brought each row (None: the game). An add-on's row is made **inactive** the
    first time it is seen and its ``active`` is never touched again here: components start off, and switching one is not the
    seed's to undo. The game's own rows follow the seed, as always.
    """
    checked = check_seed(seed, stats or STATS, resources or RESOURCES, drop_scale or DROP_SCALE)
    brought_grants = (owners or {}).get("job_abilities", {})
    if any(owner is not None for owner in brought_grants.values()):
        # An add-on's job_abilities rows are components of their own: a job is stored with the game's grants only, and the live
        # states add the ones that are on (gamestate.components). Everything was checked together above.
        own = [raw for raw in seed["job_abilities"] if brought_grants.get(job_ability_key(raw["job"], raw["ability"])) is None]
        checked["jobs"] = check_seed({**seed, "job_abilities": own}, stats or STATS, resources or RESOURCES, drop_scale or DROP_SCALE)["jobs"]
    counts = {}
    for kind, table in TABLES.items():
        if kind not in seed:
            continue
        rows = checked[kind]
        for row in rows:
            brought = (owners or {}).get(kind, {}).get(row.key)
            await upsert(session, table.__table__, _values(kind, row, active=brought is None), ["key"], insert_only=["active"] if brought else ())
        gone = update(table).where(table.key.not_in([row.key for row in rows]))
        if kind == "monsters":
            gone = gone.where(models.Monster.authored.is_(False))  # an admin's monsters are not the seed's to retire
        await session.execute(gone.values(active=False))
        counts[kind] = len(rows)
    if "maps" in seed:
        # Maps belong to a world: the engine's, made first if this is a new database.
        hub = await ensure_start(session)
        counts["maps"] = await apply_maps(session, hub.world_id, checked["maps"])
    if "dialogs" in seed:
        counts["dialogs"] = await apply_dialogs(session, checked["dialogs"])
    if "npcs" in seed:
        hub = await ensure_start(session)
        counts["npcs"] = await apply_npcs(session, hub.world_id, checked["npcs"])
    if "realms" in seed or "settlements" in seed or "materials" in seed:
        hub = await ensure_start(session)
        counts["realms"] = await apply_realms(session, hub.world_id, checked["realms"], checked["settlements"], checked["materials"])
    if "quest_slots" in seed:
        counts["quest_slots"] = await apply_slots(session, checked["quest_slots"])
    if "quest_templates" in seed:
        counts["quest_templates"] = await apply_templates(session, checked["quest_templates"])
    # The rows were written with bulk SQL: content already loaded in this session is stale.
    session.expire_all()
    return counts
