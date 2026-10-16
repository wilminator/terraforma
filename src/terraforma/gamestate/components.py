"""The add-ons' component states: which of the rows an add-on brought are on.

A component is one row an add-on brought (an item, a monster, a job...). Components start **off**, and the live document's
``components`` section says which are on::

    {"states": {"<add-on key>": {"<kind>": {"<row key>": "on"}}}}

A row that is not listed is off. That is the first degree of off, *no new use*: it is not offered, sold, dropped, spawned or picked,
and what exists keeps working. (The second, force disuse, is a later slice and adds a state.) The document holds nothing else, so the
same states always make the same text and hash, and an entry that says what is already true is refused.

``Components`` is the engine's own section (``create_app`` registers it ahead of the game's). Committing, and every start, sets the
``active`` column of the add-on rows to match the live states, which is what every offer, shop, drop, spawn and job pick already
reads, so none of them knows add-ons exist. The check refuses a document with an on row that needs one that is off.
"""

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import update

from ..addons import SWITCHABLE, Integrated, seed_key
from ..content import models
from ..content.loader import TABLES
from ..content.schema import check_seed
from .document import Section

NAME = "components"
ON = "on"
STATES = (ON,)

#: Rows are switched in groups this big, so a statement never holds more values than a database allows.
CHUNK = 500


@dataclass(frozen=True)
class Row:
    kind: str
    key: str


@dataclass
class Facts:
    """What the seed says about the add-ons' rows (read once, on first use)."""

    #: kind -> row key -> add-on key, for the rows add-ons brought.
    owned: dict[str, dict[str, str]] = field(default_factory=dict)
    #: (kind, row key) -> the section the row sits in (None: directly under the kind).
    placed: dict[Row, str | None] = field(default_factory=dict)
    #: (add-on, kind) -> section key -> the keys of that section and every section inside it.
    subtrees: dict[tuple[str, str], dict[str, set[str]]] = field(default_factory=dict)
    #: A row -> the rows it cannot work without.
    needs: dict[Row, list[Row]] = field(default_factory=dict)
    #: For a job an add-on's ``job_abilities`` rows add to: the abilities it has without them, as stored (the game's and its own)...
    base_abilities: dict[str, list[dict]] = field(default_factory=dict)
    #: ...and the rows' keys with what each adds, in the seed's order.
    grants: dict[str, list[tuple[str, dict]]] = field(default_factory=dict)


def _references(kind: str, row) -> Iterator[Row]:
    """The rows of other kinds that $row (a checked seed row) names and cannot work without."""
    if kind == "job_abilities":
        yield Row("jobs", row.job)
        yield Row("abilities", row.ability)
    elif kind == "jobs":
        yield from (Row("abilities", entry.ability) for entry in row.abilities)
    elif kind == "monsters":
        yield from (Row("abilities", key) for key in row.abilities)
        yield from (Row("items", key) for key in [*row.items, *row.equipment])
        yield from (Row("drop_tables", key) for key in row.drops)
        yield Row("personalities", row.personality)
    elif kind == "drop_tables":
        yield from (Row("items", entry.item) for entry in row.entries if entry.item is not None)
    elif kind in ("abilities", "items"):
        effect = row.effect if kind == "abilities" else row.use_effect
        if effect is not None and effect.status is not None:
            yield Row("statuses", effect.status)


class Components(Section):
    """The section of the add-ons' component states (see the module). $integrated is the game with its listed add-ons; $game gives
    the rules the seed is checked with."""

    name = NAME

    def __init__(self, integrated: Integrated, game):
        self.integrated = integrated
        self.game = game
        self._facts: Facts | None = None

    def default(self) -> dict:
        return {"states": {}}

    # --- what the seed says ---------------------------------------------------------------------------------------

    def facts(self) -> Facts:
        if self._facts is None:
            self._facts = self._read()
        return self._facts

    def _read(self) -> Facts:
        facts = Facts()
        if not self.integrated.addons:
            return facts
        layered = self.integrated.load_seed()
        rules = self.game.rules
        checked = check_seed(layered.rows, rules.stats, rules.resource_names, rules.drop_chance_scale)
        for kind in SWITCHABLE:
            for raw in layered.rows.get(kind, []):
                key = seed_key(kind, raw)
                if (addon := layered.owners.get(kind, {}).get(key)) is not None:
                    facts.owned.setdefault(kind, {})[key] = addon
                    facts.placed[Row(kind, key)] = raw.get("section")
            for row in checked.get(kind, []):
                if row.key in facts.owned.get(kind, {}):
                    facts.needs[Row(kind, row.key)] = list(_references(kind, row))
        if facts.owned.get("job_abilities"):
            ours = [raw for raw in layered.rows["job_abilities"] if seed_key("job_abilities", raw) not in facts.owned["job_abilities"]]
            bare = check_seed({**layered.rows, "job_abilities": ours}, rules.stats, rules.resource_names, rules.drop_chance_scale)["jobs"]
            for row in checked["job_abilities"]:
                if row.key in facts.owned["job_abilities"]:
                    facts.grants.setdefault(row.job, []).append((row.key, {"ability": row.ability, "level": row.level}))
            facts.base_abilities = {row.key: [entry.model_dump() for entry in row.abilities] for row in bare if row.key in facts.grants}
            for row in bare:  # (an add-on's job needs the abilities it has itself, not the ones another row adds to it)
                if Row("jobs", row.key) in facts.needs:
                    facts.needs[Row("jobs", row.key)] = list(_references("jobs", row))
        for addon in self.integrated.addons:
            for kind, nodes in addon.sections.items():
                facts.subtrees[(addon.key, kind)] = {node.key: set(node.keys()) for top in nodes for node in _walk(top)}
        return facts

    def kinds(self, addon: str) -> list[str]:
        """The kinds of component $addon brought rows of, in the engine's order."""
        return [kind for kind in SWITCHABLE if addon in self.facts().owned.get(kind, {}).values()]

    def rows_of(self, addon: str, kind: str, section: str | None = None) -> list[str]:
        """The keys of $addon's rows of $kind, in the seed's order; with $section, only those in it or in a section inside it.
        Raises ValueError for a kind the add-on has no rows of or a section it does not declare."""
        facts = self.facts()
        keys = [key for key, owner in facts.owned.get(kind, {}).items() if owner == addon]
        if not keys:
            raise ValueError(f"the add-on {addon!r} brought no {kind}")
        if section is None:
            return keys
        wanted = facts.subtrees.get((addon, kind), {}).get(section)
        if wanted is None:
            raise ValueError(f"the add-on {addon!r} declares no {kind} section {section!r}")
        return [key for key in keys if facts.placed[Row(kind, key)] in wanted]

    # --- changing a value -----------------------------------------------------------------------------------------

    def switch(self, value: dict, addon: str, on: bool, kind: str | None = None, section: str | None = None) -> dict:
        """$value with the components of $addon turned on or off, as a new value in the form the check accepts. The scope is the
        whole add-on, one $kind of it, or one $section of a kind (and the sections inside it): the bulk switches at each level of
        the admin's tree. Raises ValueError for what the add-on does not have."""
        if section is not None and kind is None:
            raise ValueError("a section belongs to a kind")
        kinds = [kind] if kind is not None else self.kinds(addon)
        if not kinds:
            raise ValueError(f"the add-on {addon!r} brought no components")
        result = copy.deepcopy(value)
        states = result.setdefault("states", {})
        for each in kinds:
            keys = self.rows_of(addon, each, section)
            held = states.setdefault(addon, {}).setdefault(each, {})
            for key in keys:
                if on:
                    held[key] = ON
                else:
                    held.pop(key, None)
            if not held:
                del states[addon][each]
        if not states[addon]:
            del states[addon]
        return result

    # --- the check ------------------------------------------------------------------------------------------------

    def check(self, value: dict, document: dict) -> list[str]:
        if set(value) != {"states"} or not isinstance(value["states"], dict):
            return ["the components section must hold exactly 'states', an object"]
        facts = self.facts()
        listed = {addon.key for addon in self.integrated.addons}
        problems: list[str] = []
        on: set[Row] = set()
        for addon, by_kind in sorted(value["states"].items()):
            if addon not in listed:
                problems.append(f"the add-on {addon!r} is not listed in the settings")
            elif not isinstance(by_kind, dict) or not by_kind:
                problems.append(f"{addon}: must be an object with the kinds that have a component on (leave the add-on out when none is)")
            else:
                for kind, rows in sorted(by_kind.items()):
                    problems += self._check_kind(facts, addon, kind, rows, on)
        for row in sorted(on, key=lambda each: (each.kind, each.key)):
            for needed in facts.needs.get(row, []):
                maker = facts.owned.get(needed.kind, {}).get(needed.key)
                if maker is not None and needed not in on:
                    problems.append(f"{facts.owned[row.kind][row.key]}: {row.kind} {row.key!r} is on, but it needs {needed.kind} {needed.key!r}"
                                    + (f" (of {maker})" if maker != facts.owned[row.kind][row.key] else "") + ", which is off")
        return problems

    @staticmethod
    def _check_kind(facts: Facts, addon: str, kind: str, rows, on: set[Row]) -> list[str]:
        if kind not in SWITCHABLE or addon not in facts.owned.get(kind, {}).values():
            return [f"{addon}: the add-on brought no {kind}"]
        if not isinstance(rows, dict) or not rows:
            return [f"{addon}: {kind} must be an object with the rows that are on (leave the kind out when none is)"]
        problems = []
        for key, state in sorted(rows.items()):
            if facts.owned[kind].get(key) != addon:
                problems.append(f"{addon}: it brought no {kind} row {key!r}")
            elif state not in STATES:
                problems.append(f"{addon}: {kind} {key!r} is {state!r}; the only state is 'on' (a row not listed is off)")
            else:
                on.add(Row(kind, key))
        return problems

    # --- making the database match ----------------------------------------------------------------------------------

    async def apply(self, db, value: dict) -> None:
        """Sets ``active`` on every add-on row: on exactly when the live states say it is on."""
        states = value.get("states", {})
        facts = self.facts()
        for job, grants in facts.grants.items():
            held = states_of(states, facts.owned["job_abilities"], "job_abilities")
            abilities = [*facts.base_abilities[job], *(grant for key, grant in grants if key in held)]
            await db.execute(update(models.Job).where(models.Job.key == job).values(abilities=abilities).execution_options(synchronize_session=False))
        for kind, owners in facts.owned.items():
            if kind == "job_abilities":
                continue  # (no table: it changed the job's abilities above)
            table = TABLES[kind]
            on = sorted(key for key, addon in owners.items() if states.get(addon, {}).get(kind, {}).get(key) == ON)
            off = sorted(set(owners) - set(on))
            for active, keys in ((True, on), (False, off)):
                for start in range(0, len(keys), CHUNK):
                    await db.execute(update(table).where(table.key.in_(keys[start:start + CHUNK])).values(active=active)
                                     .execution_options(synchronize_session=False))


def states_of(states: dict, owners: dict[str, str], kind: str) -> set[str]:
    """The keys of the $owners' rows of $kind that the live $states say are on."""
    return {key for key, addon in owners.items() if states.get(addon, {}).get(kind, {}).get(key) == ON}


def _walk(node) -> Iterator:
    yield node
    for child in node.children:
        yield from _walk(child)
