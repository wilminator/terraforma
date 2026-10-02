"""A fight's layout: parties of groups of fighters, addressed (party, group, character).

``FighterRef`` in the server calls and ``Address`` here are the same three
numbers. The numbers are keys, not positions: a fighter that leaves a group
doesn't renumber the rest.
"""

from dataclasses import dataclass, field

from .combatant import Address, Combatant
from .rules import Rules
from .status import StatusSpec


@dataclass
class Group:
    characters: dict[int, Combatant] = field(default_factory=dict)

    def dead(self, rules: Rules) -> bool:
        return not any(character.alive(rules) for character in self.characters.values())


@dataclass
class Party:
    groups: dict[int, Group] = field(default_factory=dict)
    #: The parties (by number) this one counts as allies and as enemies. A party in neither list is neutral to it.
    #: None means "not set": see ``Rules.alignment`` for what that comes to.
    allies: set[int] | None = None
    enemies: set[int] | None = None
    #: The players' teams in this party: team id -> the ids of its heroes (``Combatant.charid``). Only fighters on
    #: a team earn experience; monsters and NPCs, which are on none, do not (but still count in the shares).
    teams: dict[int, list[int]] = field(default_factory=dict)

    def dead(self, rules: Rules) -> bool:
        return all(group.dead(rules) for group in self.groups.values())


@dataclass
class Fight:
    parties: dict[int, Party] = field(default_factory=dict)
    #: The statuses this fight knows, by key: what an effect that places one looks up.
    statuses: dict[str, StatusSpec] = field(default_factory=dict)
    #: Set once the fight has ended and been paid out (the ``FightOver`` event): nothing more is played.
    over: bool = False

    def get(self, address: Address) -> Combatant:
        party, group, character = address
        try:
            return self.parties[party].groups[group].characters[character]
        except KeyError as error:
            raise KeyError(f"there is no fighter at {address}") from error

    def addresses(self) -> list[Address]:
        return [
            (party_index, group_index, character_index)
            for party_index, party in self.parties.items()
            for group_index, group in party.groups.items()
            for character_index in group.characters
        ]

    def address_of(self, fighter: Combatant) -> Address:
        for address in self.addresses():
            if self.get(address) is fighter:
                return address
        return (-1, -1, -1)

    def live_parties(self, rules: Rules) -> int:
        return sum(1 for party in self.parties.values() if not party.dead(rules))


def build_fight(layout: dict[int, dict[int, list[Combatant]]], statuses: dict[str, StatusSpec] | None = None) -> Fight:
    """A fight from ``{party: {group: [combatants...]}}``, numbering each group's fighters 0, 1, 2, ...
    $statuses are the statuses it knows, by key (``fights.content.status_spec`` makes them from the content)."""
    return Fight({
        party: Party({group: Group(dict(enumerate(members))) for group, members in groups.items()})
        for party, groups in layout.items()
    }, dict(statuses or {}))
