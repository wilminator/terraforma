"""The seed file formats: what a game's JSON may say, checked strictly.

These formats are a public interface (the license exception covers them),
so changing one is deliberate and documented in the README. Each file is a
list of objects; the file name says the kind (abilities.json, items.json,
jobs.json, personalities.json, monsters.json). Unknown fields, wrong types
and bad names are refused with the file, the row and the field named.

Stats are the engine's ten (STATS). A row names others by ``key``.
Pictures and sounds are file names under the game's assets folder: plain
names and folders, never absolute paths or "..".
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

STATS = ("HP", "MP", "Speed", "Accuracy", "Strength", "Dodge", "Block", "Power", "Resistance", "Focus")

KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ASSET = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_./-]{0,127}$")


class ContentError(ValueError):
    """The seed is wrong: the message lists every problem found."""


def _key(value: str) -> str:
    if not KEY.fullmatch(value):
        raise ValueError("a key is 1-64 lowercase letters, digits, _ or -, starting with a letter or digit")
    return value


def _asset(value: str) -> str:
    if not ASSET.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("an asset is a plain file name under the game's assets folder")
    return value


def _stats(value: dict) -> dict:
    unknown = set(value) - set(STATS)
    if unknown:
        raise ValueError(f"unknown stats {sorted(unknown)}: the stats are {', '.join(STATS)}")
    return {stat: value.get(stat, 0) for stat in STATS}


Key = Annotated[str, AfterValidator(_key)]
Asset = Annotated[str, AfterValidator(_asset)]
Stats = Annotated[dict[str, int], AfterValidator(_stats)]
Growth = Annotated[dict[str, float], AfterValidator(_stats)]
Targets = Literal["individual", "group", "party", "all_parties", "all_enemies", "all_allies"]
Effect = Literal[
    "none", "heal", "hurt", "revive", "slay", "increase_stats", "decrease_stats", "steal_stats",
    "cause_good_status", "remove_good_status", "cause_bad_status", "remove_bad_status", "restore_mp",
]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, validate_default=True)


class EffectSpec(Strict):
    """What using an ability or item does. ``attribute`` is the game's own kind of damage (fire, holy...)."""

    effect: Effect = "none"
    targets: Targets = "individual"
    base: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    attribute: str = Field(default="none", max_length=32)


class Presentation(Strict):
    """An animation: pictures and sounds with how long each shows (milliseconds)."""

    animation: str = Field(default="static_individual_impact", max_length=64)
    images: list[Asset] = []
    sounds: list[Asset] = []
    times: list[int] = Field(default=[100, 100], max_length=64)


class Ability(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["spell", "skill"]
    mp_cost: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=255)
    icon: Asset | Literal[""] = ""
    effect: EffectSpec = EffectSpec()
    presentation: Presentation = Presentation()


class Attack(Strict):
    """What an equipped weapon does when it attacks."""

    targets: Targets = "individual"
    count: int = Field(default=1, ge=1)
    attribute: str = Field(default="none", max_length=32)
    ammo_type: str = Field(default="", max_length=32)


class Item(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    price: int = Field(default=0, ge=0)
    one_use: bool = False
    description: str = Field(default="", max_length=255)
    icon: Asset | Literal[""] = ""
    use_effect: EffectSpec | None = None
    equip_slots: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]] | None = None
    stat_bonus: Stats = {}
    stat_percent: Stats = {}
    attack: Attack | None = None
    use_presentation: Presentation = Presentation()
    fight_presentation: Presentation = Presentation()


class Job(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    xp_needed: int = Field(default=0, ge=0)
    stat_growth: Growth = {}
    abilities: list[Key] = []


class Facing(Strict):
    images: list[Asset] = []
    times: list[int] = []


class Directions(Strict):
    up: Facing = Facing()
    down: Facing = Facing()
    left: Facing = Facing()
    right: Facing = Facing()


class Overworld(Strict):
    stand: Directions = Directions()
    move: Directions = Directions()


class Personality(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    base: Presentation = Presentation()
    equip: Presentation = Presentation()
    flee: Presentation = Presentation()
    hit: Presentation = Presentation()
    die: Presentation = Presentation()
    attack_close: Presentation = Presentation()
    attack_throw: Presentation = Presentation()
    attack_shoot: Presentation = Presentation()
    skill: Presentation = Presentation()
    spell: Presentation = Presentation()
    item: Presentation = Presentation()
    overworld: Overworld = Overworld()


class MonsterAi(Strict):
    """The game's own numbers steering the monster's choices (what they mean is up to the game's fight rules)."""

    action: int = Field(default=0, ge=0)
    goal: int = Field(default=0, ge=0)
    target: int = Field(default=0, ge=0)
    experience: int = Field(default=0, ge=0)


class Monster(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    personality: Key
    xp_reward: int = Field(default=0, ge=0)
    gold_reward: int = Field(default=0, ge=0)
    stats: Stats = {}
    abilities: list[Key] = []
    items: list[Key] = []
    equipment: list[Key] = []
    ai: MonsterAi = MonsterAi()


# File name -> the format its rows follow.
KINDS: dict[str, type[Strict]] = {
    "abilities": Ability,
    "items": Item,
    "personalities": Personality,
    "jobs": Job,
    "monsters": Monster,
}


def check_seed(seed: dict[str, list[dict]]) -> dict[str, list[Strict]]:
    """Every content file in $seed (as load_seed returns it) turned into checked rows.

    Other files in the seed are none of the content's business and are left alone.
    Raises ContentError listing every problem: bad fields, repeated keys and
    references to keys that don't exist.
    """
    problems: list[str] = []
    checked: dict[str, list[Strict]] = {}
    for kind, model in KINDS.items():
        rows = []
        for number, raw in enumerate(seed.get(kind, []), start=1):
            try:
                rows.append(model.model_validate(raw))
            except ValidationError as error:
                for detail in error.errors():
                    where = ".".join(str(part) for part in detail["loc"])
                    problems.append(f"{kind}.json row {number} ({raw.get('key', '?')}): {where}: {detail['msg']}")
        checked[kind] = rows
    keys: dict[str, set[str]] = {}
    for kind, rows in checked.items():
        seen: set[str] = set()
        for row in rows:
            if row.key in seen:
                problems.append(f"{kind}.json: key {row.key!r} is used twice")
            seen.add(row.key)
        keys[kind] = seen
    references = [
        ("jobs", "abilities", "abilities"),
        ("monsters", "abilities", "abilities"),
        ("monsters", "items", "items"),
        ("monsters", "equipment", "items"),
        ("monsters", "personality", "personalities"),
    ]
    for kind, field, target in references:
        for row in checked[kind]:
            value = getattr(row, field)
            for name in value if isinstance(value, list) else [value]:
                if name not in keys[target]:
                    problems.append(f"{kind}.json {row.key}: {field} names {name!r}, which {target}.json doesn't have")
    if problems:
        raise ContentError("the seed has problems:\n  " + "\n  ".join(problems))
    return checked
