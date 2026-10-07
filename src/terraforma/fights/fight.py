"""A fight's layout: parties of groups of fighters, addressed (party, group, character).

``FighterRef`` in the server calls and ``Address`` here are the same three
numbers. The numbers are keys, not positions: a fighter that leaves a group
doesn't renumber the rest.
"""

from dataclasses import dataclass, field

from .combatant import Address, Combatant
from .drops import DropTable
from .rules import Rules
from .status import StatusSpec


@dataclass
class Group:
    characters: dict[int, Combatant] = field(default_factory=dict)

    def dead(self, rules: Rules) -> bool:
        """Nobody here is left in the fight: all down, or fled."""
        return not any(character.present(rules) for character in self.characters.values())


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
        """Nobody of the party is left in the fight (all down or fled): it has left, and plays no more."""
        return all(group.dead(rules) for group in self.groups.values())

    def members(self) -> list[Combatant]:
        return [character for group in self.groups.values() for character in group.characters.values()]

    def survives(self, rules: Rules) -> bool:
        """Whether anyone of the party is alive, those who fled included."""
        return any(member.alive(rules) for member in self.members())

    def lost(self, rules: Rules) -> bool:
        """Whether the party suffered a loss: some of it stayed to the end and every one that stayed is down. A party
        that all fled has lost nothing, and one with no one in it could lose nothing."""
        staying = [member for member in self.members() if not member.fled]
        return bool(staying) and not any(member.alive(rules) for member in staying)


@dataclass
class Fight:
    parties: dict[int, Party] = field(default_factory=dict)
    #: The statuses this fight knows, by key: what an effect that places one looks up.
    statuses: dict[str, StatusSpec] = field(default_factory=dict)
    #: Set once the fight has ended and been paid out (the ``FightOver`` event): nothing more is played.
    over: bool = False
    #: The drop tables this fight knows, by key (a monster's ``drops`` name them), and the keys of the area's own tables
    #: (the map's), rolled when the fight ends (``fights.drops``).
    drop_tables: dict[str, DropTable] = field(default_factory=dict)
    area_drops: list[str] = field(default_factory=list)
    #: Whether fighters may flee (or be ejected) at all: the map's zone or a scripted fight can turn it off.
    can_flee: bool = True

    def drop_item(self, key: str):
        """The item a drop table names by $key."""
        for table in self.drop_tables.values():
            for entry in table.entries:
                if entry.item is not None and entry.item.key == key:
                    return entry.item
        raise KeyError(f"no drop table of this fight names the item {key!r}")

    def team_of(self, address: Address) -> int | None:
        """The id of the player's team the fighter at $address is on, or None (a monster, or a fighter on no team)."""
        charid = self.get(address).charid
        for team, heroes in self.parties[address[0]].teams.items():
            if charid is not None and charid in heroes:
                return team
        return None

    def team_survives(self, party: int, team: int, rules: Rules) -> bool:
        """Whether a team of $party has a hero alive (one that fled counts): only such a team gets gold and drops."""
        members = self.parties[party].teams.get(team, [])
        return any(
            fighter.alive(rules) for fighter in self.parties[party].members() if fighter.charid is not None and fighter.charid in members
        )

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


def build_fight(layout: dict[int, dict[int, list[Combatant]]], statuses: dict[str, StatusSpec] | None = None,
                drop_tables: dict[str, DropTable] | None = None, area_drops: list[str] | None = None) -> Fight:
    """A fight from ``{party: {group: [combatants...]}}``, numbering each group's fighters 0, 1, 2, ...
    $statuses are the statuses it knows, by key (``fights.content.status_spec`` makes them from the content), $drop_tables
    the drop tables by key and $area_drops the keys of the area's own."""
    return Fight({
        party: Party({group: Group(dict(enumerate(members))) for group, members in groups.items()})
        for party, groups in layout.items()
    }, dict(statuses or {}), drop_tables=dict(drop_tables or {}), area_drops=list(area_drops or []))
