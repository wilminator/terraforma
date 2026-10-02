"""Turning content rows (items, abilities, monsters) into what a fight uses. Plain attribute reads, no queries."""

from .combatant import Combatant
from .gear import EquipOutcome
from .rules import Rules
from .specs import AbilitySpec, EffectSpec, ItemSpec, scope_number
from .status import Curve, Modifier, StatusSpec, Tick


def item_spec(item) -> ItemSpec:
    attack = item.attack or {}
    return ItemSpec(
        key=item.key, name=item.name, equip_slots=tuple(item.equip_slots or ()), one_use=item.one_use,
        use_effect=EffectSpec.from_dict(item.use_effect), stat_bonus=dict(item.stat_bonus), stat_percent=dict(item.stat_percent),
        attack_targets=scope_number(attack.get("targets", 0)), attack_count=attack.get("count", 1),
        attack_attribute=attack.get("attribute", "none"), ammo_type=attack.get("ammo_type", ""),
    )


def ability_spec(ability) -> AbilitySpec:
    return AbilitySpec(ability.key, ability.name, ability.kind, ability.mp_cost, EffectSpec.from_dict(ability.effect))


def status_spec(status) -> StatusSpec:
    """A status from its content row."""
    curve = status.intensity
    return StatusSpec(
        key=status.key, name=status.name, kind=status.kind, duration=status.duration,
        curve=Curve(curve["shape"], curve["high"], curve["low"]),
        ticks=tuple(Tick(**tick) for tick in status.ticks),
        modifiers=tuple(Modifier(**modifier) for modifier in status.modifiers),
        xp_share=status.xp_share,
    )


def monster_combatant(monster, items: dict, abilities: dict, rules: Rules | None = None) -> Combatant:
    """A fighter from a monster row, with its gear on. $items and $abilities map keys to content rows."""
    stats = dict(monster.stats)
    fighter = Combatant(
        name=monster.name, base=dict(stats), current=dict(stats),
        abilities=[ability_spec(abilities[key]) for key in monster.abilities],
        inventory=[[item_spec(items[key]), 1] for key in monster.items],
    )
    for key in monster.equipment:
        fighter.inventory.append([item_spec(items[key]), 1])
        index = len(fighter.inventory) - 1
        for side in (0, 1):
            if fighter.equip(index, side).outcome is EquipOutcome.SUCCESS:
                break
    return fighter
