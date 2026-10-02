"""One fighter in a fight: its stats, gear, abilities and what it has decided to do.

Plain data and rules, no database. Heroes and monsters both become one
(``fights.content`` builds them). The equipment follows DragonStar: the
inventory is a list of ``[item, quantity]`` stacks, and equipment maps a slot
name to the *index* of a stack, so a two-handed weapon has the same index in
both hands. Removing a stack closes the list up and renumbers the equipment.
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .gear import AMMO_SLOTS, EquipOutcome, EquipResult, equipment_bonus, find_slot
from .rules import Rules
from .specs import AbilitySpec, ItemSpec
from .status import StatusToken, stat_bonus

Address = tuple[int, int, int]

HAND_SLOTS = ("lhand", "rhand", "lammo", "rammo")


class Command(IntEnum):
    ATTACK_LEFT = 0
    ATTACK_RIGHT = 1
    ITEM = 2
    EQUIP = 3
    SKILL = 4
    SPELL = 5
    DEFEND = 6
    RUN = 7
    EQUIP_AMMO = 8


@dataclass
class Combatant:
    name: str
    base: dict[str, int]
    current: dict[str, int]
    abilities: list[AbilitySpec] = field(default_factory=list)
    inventory: list[list] = field(default_factory=list)  # [ItemSpec, quantity]
    equipment: dict[str, int | None] = field(default_factory=dict)  # slot -> inventory index
    #: Who plays it, if anyone (a hero's id). None: a monster or NPC.
    charid: int | None = None
    #: The monster's key in the seed, if it is one.
    monster: str | None = None
    #: The statuses it is under (fights.status).
    tokens: list[StatusToken] = field(default_factory=list)
    # What it has decided to do this round.
    command: int = Command.DEFEND
    using: int = 0
    target: Address = (0, 0, 0)
    #: How the computer plays it (``fights.ai``): the numbers of a monster's ``ai`` block in the seed.
    ai_action: int = 0
    ai_goal: int = 0
    ai_target: int = 0
    ai_experience: int = 0
    # Experience and advancement (``fights.rewards``). ``job_need`` is the job's ``xp_needed`` and ``growth`` its
    # stat growth per level; ``gold`` is what a monster drops, and ``xp_debts`` what it owes others for what they did
    # to it: ``[party, group, character, ratio, pxp]``, the one owed, the share of the gauge moved, and its own PXP.
    level: int = 1
    exp: int = 0
    job_need: int = 0
    growth: dict[str, float] = field(default_factory=dict)
    gold: int = 0
    xp_debts: list[list] = field(default_factory=list)
    #: The drop tables a monster rolls when it dies (keys; ``fights.drops``), and who placed a good status on this
    #: fighter (they count as having helped it when drops are shared out).
    drops: tuple[str, ...] = ()
    buffed_by: list[Address] = field(default_factory=list)

    # --- stats ----------------------------------------------------------------------------
    def alive(self, rules: Rules) -> bool:
        return self.current[rules.vital] > 0

    def worn(self, command: int | bool | None = None, every: bool = False, stat: str = "", rules: Rules | None = None) -> list[ItemSpec]:
        """The items worn that count towards $stat: all of them, except a hand's gear counts for a hand stat
        only when that hand acts ($command 0 is left, 1 right, anything else neither)."""
        chosen = {}
        hand_stat = rules is not None and stat in rules.hand_stats
        for slot, index in self.equipment.items():
            if index is None:
                continue
            if every or not hand_stat:
                keep = True
            elif command == Command.ATTACK_LEFT:
                keep = slot not in ("rammo", "rhand")
            elif command == Command.ATTACK_RIGHT:
                keep = slot not in ("lammo", "lhand")
            else:
                keep = slot not in HAND_SLOTS
            if keep:
                chosen[index] = self.inventory[index][0]
        return list(chosen.values())

    def with_gear(self, rules: Rules, stat: str, value: int, command=None, every: bool = False) -> int:
        """$value for $stat with the worn gear's percentages applied first, then its flat bonuses."""
        return equipment_bonus(self.worn(command, every, stat, rules), stat, value)

    def get_base(self, rules: Rules, stat: str, all_equipment: bool | int = False) -> int:
        """The stat's usual value: for resources the maximum, which counts gear; for others the bare base unless
        $all_equipment says to count gear (True: all of it; a command number: as for that command)."""
        value = self.base[stat]
        if all_equipment is not False or stat in rules.resource_names:
            every = all_equipment is True
            command = all_equipment if not isinstance(all_equipment, bool) else self.command
            value = self.with_gear(rules, stat, value, command, every)
        return value

    def get_current(self, rules: Rules, stat: str, all_equipment: bool | int = False) -> int:
        """The stat right now. Resources are what is left of them; every other stat counts gear."""
        value = self.current[stat]
        if stat not in rules.resource_names:
            every = all_equipment is True
            command = all_equipment if not isinstance(all_equipment, bool) else self.command
            value = self.with_gear(rules, stat, value, command, every) + stat_bonus(self, stat)
        return value

    def reset_stats(self, rules: Rules, everything: bool = False) -> None:
        """Current stats back to base (the resources only if $everything: HP and MP carry over between fights)."""
        for stat, value in self.base.items():
            if everything or stat not in rules.resource_names:
                self.current[stat] = value

    # --- gear --------------------------------------------------------------------------------
    def equipped(self, slot: str) -> ItemSpec | None:
        index = self.equipment.get(slot)
        return None if index is None else self.inventory[index][0]

    def hands_share_weapon(self) -> bool:
        left, right = self.equipment.get("lhand"), self.equipment.get("rhand")
        return left is not None and right is not None and left == right

    def weapon(self, rules: Rules, side: int) -> ItemSpec | None:
        """What the hand on $side holds (the unarmed default for an empty hand), or None for the right hand of a
        two-handed weapon, which only the left hand swings."""
        held = self.equipped(find_slot("hand", side))
        if held is None:
            return rules.unarmed
        if side == 1 and self.hands_share_weapon():
            return None
        return held

    def ammo(self, side: int) -> ItemSpec | None:
        """The ammunition for the weapon on $side; None if there is none, or the weapon is its own ammunition."""
        ammo = self.equipped(find_slot("ammo", side))
        if ammo is not None and self.equipment.get(find_slot("hand", side)) == self.equipment.get(find_slot("ammo", side)):
            return None
        return ammo

    def weapon_effect(self, rules: Rules, command: int):
        side = 0 if command == Command.ATTACK_LEFT else 1
        strength = self.get_current(rules, "Strength", command)
        striker = self.ammo(side) or self.weapon(rules, side)
        return rules.weapon_effect(striker, strength)

    def attack_count(self, side: int) -> int:
        """How many times the weapon in that hand strikes (and so how many times it appears in the order)."""
        held = self.equipped(find_slot("hand", side))
        return held.attack_count if held is not None else 1

    def expend_ammo(self, side: int) -> bool | int:
        """True: nothing to spend. False: out of ammunition. Otherwise the inventory index of the stack to spend one from
        (it is spent after the attack, since the last one may still be needed to work out the damage)."""
        slot_side = "l" if side == 0 else "r"
        weapon_index = self.equipment.get(slot_side + "hand")
        if weapon_index is None:
            return True
        ammo_index = self.equipment.get(slot_side + "ammo")
        weapon = self.inventory[weapon_index][0]
        if weapon.ammo_type == "" and ammo_index is None:
            return True
        if ammo_index is None:
            return False
        if self.inventory[ammo_index][1] > 0:
            return ammo_index
        return False

    # --- the inventory ---------------------------------------------------------------------------
    def add_item(self, rules: Rules, item: ItemSpec, qty: int) -> int:
        """Puts $qty of $item in the inventory, as ``heroes.inventory.add_item`` does (non-equipment and ammunition stack up
        to ``Rules.stack_size``, gear never, in at most ``Rules.inventory_stacks`` stacks). Returns how many did not fit."""
        stackable = not item.equip_slots or any(slot in AMMO_SLOTS for slot in item.equip_slots)
        if stackable:
            for stack in self.inventory:
                if stack[0].key == item.key and stack[1] < rules.stack_size and qty:
                    moved = min(qty, rules.stack_size - stack[1])
                    stack[1] += moved
                    qty -= moved
        while qty and len(self.inventory) < rules.inventory_stacks:
            size = min(qty, rules.stack_size) if stackable else 1
            self.inventory.append([item, size])
            qty -= size
        return qty

    def remove_item(self, index: int, qty: int) -> int:
        """Takes $qty from the stack at $index (all of it if that's the lot, closing the list up). Returns how many went."""
        if not 0 <= index < len(self.inventory):
            return 0
        stack = self.inventory[index]
        if stack[1] > qty:
            stack[1] -= qty
            return qty
        removed = stack[1]
        del self.inventory[index]
        for slot, held in list(self.equipment.items()):
            if held is None or held == index:
                self.equipment[slot] = None
            elif held > index:
                self.equipment[slot] = held - 1
        return removed

    def equip(self, index: int, side: int = 0):
        """Wields or wears the stack at $index. Same answers as heroes.inventory.equip: success with the slots,
        or what is in the way."""
        if not 0 <= index < len(self.inventory):
            return EquipResult(EquipOutcome.NOT_FOUND)
        item = self.inventory[index][0]
        if not item.equip_slots:
            return EquipResult(EquipOutcome.NOT_EQUIPABLE)
        slots = list(item.equip_slots)
        for slot in slots:
            held = self.equipment.get(find_slot(slot, side))
            if held is not None:
                return EquipResult(EquipOutcome.NEEDS_UNEQUIPPING, occupying_position=held)
        for slot, held in self.equipment.items():
            if held == index and slot not in slots:
                return EquipResult(EquipOutcome.NEEDS_UNEQUIPPING, occupying_position=index)
        if slots == ["ammo"]:
            weapon = self.equipped(find_slot("hand", side))
            if weapon is None or weapon.ammo_type != item.ammo_type:
                return EquipResult(EquipOutcome.INCOMPATIBLE_AMMO)
            if "lhand" in weapon.equip_slots and "rhand" in weapon.equip_slots:
                side = 0
        placed = []
        for slot in slots:
            name = find_slot(slot, side)
            self.equipment[name] = index
            placed.append(name)
        return EquipResult(EquipOutcome.SUCCESS, slots=tuple(placed))

    def unequip(self, index: int):
        """Takes the stack at $index off, and with a weapon its ammunition."""
        if not 0 <= index < len(self.inventory):
            return EquipResult(EquipOutcome.NOT_FOUND)
        item = self.inventory[index][0]
        if not item.equip_slots:
            return EquipResult(EquipOutcome.NOT_EQUIPABLE)
        freed = [slot for slot, held in self.equipment.items() if held == index]
        side = ""
        for slot in freed:
            if slot in ("lhand", "rhand"):
                side = slot[0] if side == "" else "l"
        if side and item.ammo_type and not any(slot in AMMO_SLOTS for slot in item.equip_slots):
            if self.equipment.get(f"{side}ammo") is not None:
                freed.append(f"{side}ammo")
        for slot in freed:
            self.equipment[slot] = None
        return EquipResult(EquipOutcome.SUCCESS, slots=tuple(freed))
