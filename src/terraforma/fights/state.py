"""A fight as plain JSON, and back: how it is stored and how it is replayed.

``dehydrate`` is what is written once when a fight starts (its *initial
state*); ``hydrate`` rebuilds a ``Fight`` from it. The fight at any later
moment is that snapshot plus the events recorded since (``fights.replay``).
Only plain lists, dicts, numbers and strings, so it fits a JSON column on
every database and hashes the same everywhere.
"""

from .combatant import Combatant
from .fight import Fight, Group, Party
from .specs import AbilitySpec, EffectSpec, ItemSpec
from .status import Curve, Modifier, StatusSpec, StatusToken, Tick


def _effect(effect: EffectSpec) -> dict:
    return {
        "effect": effect.effect, "targets": effect.targets, "base": effect.base, "added": effect.added, "attribute": effect.attribute,
        "stats": list(effect.stats), "status": effect.status, "duration": effect.duration,
    }


def _build_effect(raw: dict) -> EffectSpec:
    return EffectSpec(**{**raw, "stats": tuple(raw.get("stats", ()))})


def _status(spec: StatusSpec) -> dict:
    return {
        "key": spec.key, "name": spec.name, "kind": spec.kind, "duration": spec.duration, "xp_share": spec.xp_share,
        "curve": {"shape": spec.curve.shape, "high": spec.curve.high, "low": spec.curve.low},
        "ticks": [dict(vars(tick)) for tick in spec.ticks],
        "modifiers": [dict(vars(modifier)) for modifier in spec.modifiers],
    }


def _build_status(raw: dict) -> StatusSpec:
    return StatusSpec(
        key=raw["key"], name=raw["name"], kind=raw["kind"], duration=raw["duration"], xp_share=raw["xp_share"],
        curve=Curve(**raw["curve"]), ticks=tuple(Tick(**tick) for tick in raw["ticks"]),
        modifiers=tuple(Modifier(**modifier) for modifier in raw["modifiers"]),
    )


def _token(token: StatusToken) -> dict:
    return {"status": token.spec.key, "source": list(token.source), "duration": token.duration, "rounds": token.rounds, "turns": token.turns}


def _item(item: ItemSpec) -> dict:
    return {
        "key": item.key, "name": item.name, "equip_slots": list(item.equip_slots), "one_use": item.one_use,
        "use_effect": _effect(item.use_effect), "stat_bonus": dict(item.stat_bonus), "stat_percent": dict(item.stat_percent),
        "attack_targets": item.attack_targets, "attack_count": item.attack_count,
        "attack_attribute": item.attack_attribute, "ammo_type": item.ammo_type,
    }


def _ability(ability: AbilitySpec) -> dict:
    return {"key": ability.key, "name": ability.name, "kind": ability.kind, "mp_cost": ability.mp_cost, "effect": _effect(ability.effect)}


def _fighter(fighter: Combatant) -> dict:
    return {
        "name": fighter.name, "base": dict(fighter.base), "current": dict(fighter.current), "charid": fighter.charid,
        "monster": fighter.monster,
        "abilities": [_ability(ability) for ability in fighter.abilities],
        "inventory": [[_item(item), qty] for item, qty in fighter.inventory],
        "equipment": dict(fighter.equipment),
        "command": int(fighter.command), "using": fighter.using, "target": list(fighter.target),
        "tokens": [_token(token) for token in fighter.tokens],
    }


def dehydrate(fight: Fight) -> dict:
    return {"statuses": {key: _status(spec) for key, spec in fight.statuses.items()}, "parties": [
        {
            "index": party_index,
            "allies": None if party.allies is None else sorted(party.allies),
            "enemies": None if party.enemies is None else sorted(party.enemies),
            "groups": [
                {"index": group_index, "characters": [
                    {"index": character_index, **_fighter(fighter)} for character_index, fighter in group.characters.items()
                ]}
                for group_index, group in party.groups.items()
            ],
        }
        for party_index, party in fight.parties.items()
    ]}


def _build_item(raw: dict) -> ItemSpec:
    return ItemSpec(
        key=raw["key"], name=raw["name"], equip_slots=tuple(raw["equip_slots"]), one_use=raw["one_use"],
        use_effect=_build_effect(raw["use_effect"]), stat_bonus=dict(raw["stat_bonus"]), stat_percent=dict(raw["stat_percent"]),
        attack_targets=raw["attack_targets"], attack_count=raw["attack_count"],
        attack_attribute=raw["attack_attribute"], ammo_type=raw["ammo_type"],
    )


def _build_fighter(raw: dict, statuses: dict[str, StatusSpec]) -> Combatant:
    return Combatant(
        name=raw["name"], base=dict(raw["base"]), current=dict(raw["current"]),
        abilities=[AbilitySpec(a["key"], a["name"], a["kind"], a["mp_cost"], _build_effect(a["effect"])) for a in raw["abilities"]],
        inventory=[[_build_item(item), qty] for item, qty in raw["inventory"]],
        equipment=dict(raw["equipment"]), charid=raw["charid"], monster=raw.get("monster"),
        command=raw["command"], using=raw["using"], target=tuple(raw["target"]),
        tokens=[
            StatusToken(statuses[token["status"]], tuple(token["source"]), token["duration"], token["rounds"], token["turns"])
            for token in raw.get("tokens", [])
        ],
    )


def hydrate(raw: dict) -> Fight:
    statuses = {key: _build_status(spec) for key, spec in raw.get("statuses", {}).items()}
    parties = {}
    for party in raw["parties"]:
        groups = {
            group["index"]: Group({each["index"]: _build_fighter(each, statuses) for each in group["characters"]})
            for group in party["groups"]
        }
        parties[party["index"]] = Party(
            groups, None if party["allies"] is None else set(party["allies"]),
            None if party["enemies"] is None else set(party["enemies"]),
        )
    return Fight(parties, statuses)
