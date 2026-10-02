"""The seed file formats: what a game's JSON may say, checked strictly.

These formats are a public interface (the license exception covers them),
so changing one is deliberate and documented in the README. Each file is a
list of objects; the file name says the kind (abilities.json, items.json,
jobs.json, personalities.json, monsters.json, statuses.json). Unknown fields, wrong types
and bad names are refused with the file, the row and the field named.

Stats are the game's (``Rules.stats``; the engine's ten, STATS, unless it overrides them), and so are its
resources (``Rules.resource_names``; RESOURCES). A row names others by ``key``.
Pictures and sounds are file names under the game's assets folder: plain
names and folders, never absolute paths or "..".
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

STATS = ("HP", "MP", "Speed", "Accuracy", "Strength", "Dodge", "Block", "Power", "Resistance", "Focus")
#: The stats that are pools (``Rules.resource_names``): effects that push a stat's current value skip them.
RESOURCES = ("HP", "MP")
#: A drop's chance is out of this unless the game's rules say otherwise (``Rules.drop_chance_scale``).
DROP_SCALE = 10000

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


def _stats(value: dict, info: ValidationInfo) -> dict:
    """The stats are the game's own (``Rules.stats``, handed in as the validation context), the engine's ten by default."""
    names = (info.context or {}).get("stats", STATS)
    unknown = set(value) - set(names)
    if unknown:
        raise ValueError(f"unknown stats {sorted(unknown)}: the stats are {', '.join(names)}")
    return {stat: value.get(stat, 0) for stat in names}


Key = Annotated[str, AfterValidator(_key)]
Asset = Annotated[str, AfterValidator(_asset)]
Stats = Annotated[dict[str, int], AfterValidator(_stats)]
Growth = Annotated[dict[str, float], AfterValidator(_stats)]
# A name for one target or a whole group, party and so on; or a number n for the target and n neighbours each way along its group.
Targets = (
    Literal["individual", "group", "party", "all_parties", "all_enemies", "all_allies", "all_not_enemies", "all_not_allies"]
    | Annotated[int, Field(ge=0)]
)
Effect = Literal[
    "none", "heal", "hurt", "revive", "slay", "increase_stats", "decrease_stats", "steal_stats",
    "cause_good_status", "remove_good_status", "cause_bad_status", "remove_bad_status", "restore_mp",
]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, validate_default=True)


STAT_EFFECTS = ("increase_stats", "decrease_stats", "steal_stats")
CAUSE_EFFECTS = {"cause_good_status": "good", "cause_bad_status": "bad"}
REMOVE_EFFECTS = {"remove_good_status": "good", "remove_bad_status": "bad"}


class EffectSpec(Strict):
    """What using an ability or item does. ``attribute`` is the game's own kind of damage (fire, holy...).

    ``stats`` (the non-resource stats a stat effect moves; required for those) and ``status`` (the status a
    ``cause_`` effect places, required; a ``remove_`` effect takes off that one, or every one of its kind if
    left out) and ``duration`` (rounds a placed status lasts, over the status's own) are for those effects only."""

    effect: Effect = "none"
    targets: Targets = "individual"
    base: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    attribute: str = Field(default="none", max_length=32)
    stats: list[str] = Field(default=[], max_length=32)
    status: Key | None = None
    duration: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _right_fields_for_the_effect(self, info: ValidationInfo):
        context = info.context or {}
        names, resources = context.get("stats", STATS), context.get("resources", RESOURCES)
        if self.effect in STAT_EFFECTS:
            if not self.stats:
                raise ValueError(f"{self.effect} needs stats: the ones it moves")
            for stat in self.stats:
                if stat not in names or stat in resources:
                    raise ValueError(f"stats: {stat!r} is not a stat that can be pushed (the stats are {', '.join(n for n in names if n not in resources)})")
        elif self.stats:
            raise ValueError(f"stats is only for {', '.join(STAT_EFFECTS)}")
        if self.effect in CAUSE_EFFECTS:
            if self.status is None:
                raise ValueError(f"{self.effect} needs a status")
        elif self.effect not in REMOVE_EFFECTS:
            if self.status is not None or self.duration is not None:
                raise ValueError("status and duration are only for the status effects")
        if self.effect in REMOVE_EFFECTS and self.duration is not None:
            raise ValueError("duration is only for the effects that place a status")
        return self


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


class JobAbility(Strict):
    """An ability a job grants, and the level a hero must reach to have it."""

    ability: Key
    level: int = Field(default=1, ge=1)


def _job_abilities(value):
    """A bare key means "from level 1": ["slash"] and [{"ability": "slash", "level": 1}] are the same."""
    if isinstance(value, list):
        return [{"ability": entry, "level": 1} if isinstance(entry, str) else entry for entry in value]
    return value


class Job(Strict):
    key: Key
    name: str = Field(min_length=1, max_length=64)
    xp_needed: int = Field(default=0, ge=0)
    stat_growth: Growth = {}
    abilities: Annotated[list[JobAbility], BeforeValidator(_job_abilities)] = []


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


class DropEntry(Strict):
    """One thing a drop table can drop. In a plain table ``chance`` is out of the game's ``Rules.drop_chance_scale`` (10000 by
    default); in a weighted table ``weight`` picks it. ``min`` and ``max`` say how many. ``for`` says who gets it: ``one``
    (the default), ``each_team`` (one in each team of the winning side) or ``each_member`` (every hero of the winning party)."""

    item: Key | None = None
    chance: int = Field(default=0, ge=0)
    weight: int = Field(default=0, ge=0)
    min: int = Field(default=1, ge=1)
    max: int = Field(default=1, ge=1)
    share: Literal["one", "each_team", "each_member"] = Field(default="one", validation_alias="for", serialization_alias="for")

    @model_validator(mode="after")
    def _min_is_not_above_max(self):
        if self.min > self.max:
            raise ValueError("min can't be above max")
        return self


class DropTable(Strict):
    """What a monster (or an area) leaves behind: rolled once when it dies. A plain table rolls every entry on its own chance;
    a ``weighted`` one picks an entry by weight ``rolls`` times, and an entry with no item means nothing drops."""

    key: Key
    name: str = Field(min_length=1, max_length=64)
    weighted: bool = False
    rolls: int = Field(default=1, ge=1, le=100)
    entries: list[DropEntry] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _entries_fit_the_kind_of_table(self, info: ValidationInfo):
        scale = (info.context or {}).get("drop_scale", DROP_SCALE)
        if self.weighted:
            if any(entry.weight < 1 for entry in self.entries):
                raise ValueError("every entry of a weighted table needs a weight of at least 1")
            if any(entry.chance for entry in self.entries):
                raise ValueError("a weighted table picks by weight: chance is for plain tables")
            return self
        if self.rolls != 1:
            raise ValueError("rolls is for weighted tables")
        for entry in self.entries:
            if entry.item is None:
                raise ValueError("an entry of a plain table needs an item (only a weighted table can drop nothing)")
            if entry.weight:
                raise ValueError("weight is for weighted tables")
            if not 1 <= entry.chance <= scale:
                raise ValueError(f"a chance is 1 to {scale}")
        return self


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
    # Drop tables (keys) rolled when it dies.
    drops: list[Key] = []
    ai: MonsterAi = MonsterAi()


WHENS = ("round_start", "round_end", "turn_start", "turn_end", "helped", "harmed", "saving_throw")
TIMED = ("round_start", "round_end", "turn_start", "turn_end")


class Intensity(Strict):
    """How strong a status is over its life. ``flat``: always ``high``. ``falling``: ``high`` when placed, ``low`` on its
    last round. ``rising``: the other way round. Whatever the status does is scaled by it (1.0 is full strength)."""

    shape: Literal["flat", "rising", "falling"] = "flat"
    high: float = Field(default=1.0, gt=0, le=10)
    low: float = Field(default=0.0, ge=0, le=10)

    @model_validator(mode="after")
    def _low_is_not_above_high(self):
        if self.low > self.high:
            raise ValueError("low can't be above high")
        return self


class StatusTick(Strict):
    """Something a status does when ``when`` happens, every ``every``-th time (only for the four timed moments)."""

    when: Literal["round_start", "round_end", "turn_start", "turn_end", "helped", "harmed", "saving_throw"]
    action: Literal["damage", "heal", "skip_turn", "end"]
    every: int = Field(default=1, ge=1)
    # damage and heal: this much plus this percent of the maximum of the resource (life if none is named).
    amount: int = Field(default=0, ge=0)
    percent: float = Field(default=0.0, ge=0, le=100)
    resource: str = Field(default="", max_length=32)
    attribute: str = Field(default="none", max_length=32)
    # skip_turn: the chance out of 100 that the turn is lost.
    chance: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def _fits_its_moment(self, info: ValidationInfo):
        resources = (info.context or {}).get("resources", RESOURCES)
        if self.action in ("damage", "heal") and self.amount == 0 and self.percent == 0:
            raise ValueError(f"{self.action} needs an amount or a percent")
        if self.action not in ("damage", "heal") and (self.amount or self.percent or self.resource):
            raise ValueError("amount, percent and resource are only for damage and heal")
        if self.resource and self.resource not in resources:
            raise ValueError(f"resource: {self.resource!r} is not a resource (the resources are {', '.join(resources)})")
        if self.action == "skip_turn" and self.when != "turn_start":
            raise ValueError("skip_turn is only for turn_start")
        if self.action != "skip_turn" and self.chance != 100:
            raise ValueError("chance is only for skip_turn")
        if self.every > 1 and self.when not in TIMED:
            raise ValueError(f"every is only for {', '.join(TIMED)}")
        return self


class StatusModifier(Strict):
    """Something that holds while a status lasts. ``damage_taken`` and ``damage_dealt``: damage of ``attribute`` (or any, for
    "all") is multiplied by ``factor``. ``stat``: the current value of the stat is raised or lowered by ``amount``."""

    kind: Literal["damage_taken", "damage_dealt", "stat"]
    attribute: str = Field(default="all", max_length=32)
    factor: float = Field(default=1.0, ge=0, le=100)
    stat: str = Field(default="", max_length=32)
    amount: int = 0

    @model_validator(mode="after")
    def _fits_its_kind(self, info: ValidationInfo):
        context = info.context or {}
        names, resources = context.get("stats", STATS), context.get("resources", RESOURCES)
        if self.kind == "stat":
            if self.stat not in names or self.stat in resources:
                raise ValueError(f"stat: {self.stat!r} is not a stat that can be raised or lowered")
            if self.attribute != "all" or self.factor != 1.0:
                raise ValueError("attribute and factor are for the damage modifiers")
        else:
            if self.stat or self.amount:
                raise ValueError("stat and amount are for the stat modifier")
        return self


class Status(Strict):
    """A status a fighter can be under (see fights.status). ``kind`` says what ``cause_`` and ``remove_`` effects of good and bad
    statuses reach. ``duration`` is in rounds (none: until something removes it). ``xp_share`` is the share of the bearer's
    PXP, at full intensity, the one who placed it is credited for each of its ticks that moves no gauge."""

    key: Key
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["good", "bad"]
    description: str = Field(default="", max_length=255)
    icon: Asset | Literal[""] = ""
    duration: int | None = Field(default=None, ge=1)
    intensity: Intensity = Intensity()
    ticks: list[StatusTick] = Field(default=[], max_length=32)
    modifiers: list[StatusModifier] = Field(default=[], max_length=32)
    xp_share: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _a_status_without_an_end_is_flat(self):
        if self.duration is None and self.intensity.shape != "flat":
            raise ValueError("a status with no duration has no time to rise or fall over: its intensity must be flat")
        return self


# File name -> the format its rows follow.
KINDS: dict[str, type[Strict]] = {
    "abilities": Ability,
    "items": Item,
    "drop_tables": DropTable,
    "personalities": Personality,
    "jobs": Job,
    "monsters": Monster,
    "statuses": Status,
}


def check_seed(seed: dict[str, list[dict]], stats: tuple[str, ...] = STATS, resources: tuple[str, ...] = RESOURCES, drop_scale: int = DROP_SCALE) -> dict[str, list[Strict]]:
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
                rows.append(model.model_validate(raw, context={"stats": stats, "resources": resources, "drop_scale": drop_scale}))
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
        ("monsters", "drops", "drop_tables"),
        ("monsters", "personality", "personalities"),
    ]
    for kind, field, target in references:
        for row in checked[kind]:
            value = getattr(row, field)
            names = value if isinstance(value, list) else [value]
            for name in (entry.ability if isinstance(entry, JobAbility) else entry for entry in names):
                if name not in keys[target]:
                    problems.append(f"{kind}.json {row.key}: {field} names {name!r}, which {target}.json doesn't have")
    for table in checked["drop_tables"]:
        for entry in table.entries:
            if entry.item is not None and entry.item not in keys["items"]:
                problems.append(f"drop_tables.json {table.key}: entries name {entry.item!r}, which items.json doesn't have")
    kinds = {row.key: row.kind for row in checked["statuses"]}
    effects = [(kind, row, effect) for kind, field in (("abilities", "effect"), ("items", "use_effect"))
               for row in checked[kind] if (effect := getattr(row, field)) is not None]
    for kind, row, effect in effects:
        if effect.status is None:
            continue
        wanted = CAUSE_EFFECTS.get(effect.effect) or REMOVE_EFFECTS.get(effect.effect)
        if effect.status not in keys["statuses"]:
            problems.append(f"{kind}.json {row.key}: status names {effect.status!r}, which statuses.json doesn't have")
        elif kinds[effect.status] != wanted:
            problems.append(f"{kind}.json {row.key}: {effect.effect} names {effect.status!r}, which is a {kinds[effect.status]} status")
    if problems:
        raise ContentError("the seed has problems:\n  " + "\n  ".join(problems))
    return checked
