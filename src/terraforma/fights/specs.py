"""What a fight needs to know about items and abilities, as plain frozen data.

The fight code is pure (no database, no web), so it works on these small
records. ``fights.content`` builds them from the content tables.

Targets are numbers, as DragonStar has them: ``0`` is one target, a
positive number ``n`` reaches ``n`` neighbours each way along the group,
and the negative numbers are whole groups, parties and so on.
"""

from dataclasses import dataclass, field

INDIVIDUAL = 0
GROUP = -1
PARTY = -2
ALL_PARTIES = -3
ALL_ENEMIES = -4
ALL_ALLIES = -5
ALL_NOT_ENEMIES = -6
ALL_NOT_ALLIES = -7
RANDOM_PARTY = -8

SCOPES = {
    "individual": INDIVIDUAL, "group": GROUP, "party": PARTY,
    "all_parties": ALL_PARTIES, "all_enemies": ALL_ENEMIES, "all_allies": ALL_ALLIES,
    "all_not_enemies": ALL_NOT_ENEMIES, "all_not_allies": ALL_NOT_ALLIES, "random_party": RANDOM_PARTY,
}

#: The scopes that name parties by how they stand to the actor's party, rather than by position.
BY_ALIGNMENT = (ALL_PARTIES, ALL_ENEMIES, ALL_ALLIES, ALL_NOT_ENEMIES, ALL_NOT_ALLIES)

#: Every scope that is not aimed at a position: the alignment scopes, and ``RANDOM_PARTY`` (one party drawn from the
#: fight's stream, from the ones ``Rules.random_party_pool`` names).
NOT_AIMED = (*BY_ALIGNMENT, RANDOM_PARTY)

# Effects (the seed's names, which are DragonStar's, in lower case).
NONE = "none"
HEAL = "heal"
HURT = "hurt"
REVIVE = "revive"
SLAY = "slay"
INCREASE_STATS = "increase_stats"
DECREASE_STATS = "decrease_stats"
STEAL_STATS = "steal_stats"
CAUSE_GOOD_STATUS = "cause_good_status"
REMOVE_GOOD_STATUS = "remove_good_status"
CAUSE_BAD_STATUS = "cause_bad_status"
REMOVE_BAD_STATUS = "remove_bad_status"
RESTORE_MP = "restore_mp"

DETRIMENTAL = {HURT, SLAY, DECREASE_STATS, STEAL_STATS, REMOVE_GOOD_STATUS, CAUSE_BAD_STATUS}
ONLY_LIVING = {
    HEAL, HURT, SLAY, INCREASE_STATS, DECREASE_STATS, STEAL_STATS, CAUSE_GOOD_STATUS,
    REMOVE_GOOD_STATUS, CAUSE_BAD_STATUS, REMOVE_BAD_STATUS, RESTORE_MP,
}


def scope_number(targets: int | str) -> int:
    """The number for a seed's ``targets``: a name ("group") or already a number."""
    return SCOPES[targets] if isinstance(targets, str) else targets


@dataclass(frozen=True)
class EffectSpec:
    """What using something does: ``base`` plus up to ``added`` more, to ``targets``."""

    effect: str = NONE
    targets: int = INDIVIDUAL
    base: int = 0
    added: int = 0
    attribute: str = "none"
    #: The stats a stat effect moves (increase_stats, decrease_stats, steal_stats).
    stats: tuple[str, ...] = ()
    #: The status a status effect places or removes. Empty on a remove: every status of that kind.
    status: str = ""
    #: Rounds a placed status lasts, overriding the status's own. None: the status's own.
    duration: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "EffectSpec":
        if not data:
            return cls()
        return cls(data.get("effect", NONE), scope_number(data.get("targets", INDIVIDUAL)),
                   data.get("base", 0), data.get("added", 0), data.get("attribute", "none"),
                   tuple(data.get("stats") or ()), data.get("status") or "", data.get("duration"))

    @property
    def detrimental(self) -> bool:
        return self.effect in DETRIMENTAL

    @property
    def only_living(self) -> bool:
        return self.effect in ONLY_LIVING


@dataclass(frozen=True)
class ItemSpec:
    """An item as a fight sees it."""

    key: str
    name: str = ""
    equip_slots: tuple[str, ...] = ()
    one_use: bool = False
    use_effect: EffectSpec = field(default_factory=EffectSpec)
    stat_bonus: dict = field(default_factory=dict, hash=False)
    stat_percent: dict = field(default_factory=dict, hash=False)
    # When used to attack: how far it reaches, how many times it strikes, and its kind of damage.
    attack_targets: int = INDIVIDUAL
    attack_count: int = 1
    attack_attribute: str = "none"
    ammo_type: str = ""

    def weapon_effect(self, strength: int) -> EffectSpec:
        """What an attack with this does, by the wielder's Strength: half to all of it."""
        return EffectSpec(HURT, self.attack_targets, strength // 2, -(-strength // 2), self.attack_attribute)


@dataclass(frozen=True)
class AbilitySpec:
    key: str
    name: str = ""
    kind: str = "skill"  # "skill" (can be dodged) or "spell" (can be resisted)
    mp_cost: int = 0
    effect: EffectSpec = field(default_factory=EffectSpec)
