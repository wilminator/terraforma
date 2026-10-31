"""The contract terms the engine ships: the standard set, and the base for a game that adds its own.

``StandardTerms`` is the default ``Game(contracts=...)``. Its kinds are the ones the engine can judge from its own records:

* ``truce``: the bound side does not pick a fight with the other side. It is broken by any fight that began after the contract started
  in which the bound side's heroes were the attacking party (party 0, as ``pvp.service.start_pvp_fight`` seats them) against the other
  side's heroes, and it is met when the contract's time runs out. Params: ``value``, the gold it is worth (default 0).
* ``parcel``: the bound side sends the other side goods by delivery (``terraforma.delivery``). It is met once parcels the bound side sent
  to the other side with the contract's reference (purpose ``contract``, ref the contract's id as text) hold at least the gold and items
  asked for, and broken if time runs out first. Params: ``gold`` and ``items`` (a list of [item key, quantity]); ``value`` is the gold it
  is worth (the gold asked for, by default). A parcel the game called back (cancelled) does not count.

* ``assassinate``: the bound side kills a third team, which need not know or agree. Params: ``target_team_id``, ``scope`` (``"team"``: every
  hero on the target team; ``"hero"``: the one named by ``hero_id``, who must be on the target team) and ``value``. It is met once, in
  fights that began after the contract started and in which a hero of the bound side and a hero of the target were on opposite sides, the
  target's heroes the scope names have each *fallen* (a ``Died`` event; a later revive does not undo it). It is broken if time runs out first.
  The target may not be the bound side or the other side of the contract (an offer that says so is refused).

* ``kill_boss``: the bound side kills a boss (``monster``, a monster key) inside the other side's run of a quest. The other side's party is
  in an instance of the quest; ``service.enter_instance`` lets the bound side's party in (it records the instance's map on the term as
  ``entered_map_id``). It is met once a fight on that map that began after the contract started, with a hero of the bound side in it, has
  a ``Died`` event for a fighter of that monster, and broken if the party never went in or the boss stands when time runs out. Who gets
  the drops is the contract's to settle (a ``parcel`` term sends them on). Params: ``monster``, ``value``.

* ``escort_to``: the bound side takes the other side (the escorted team) to a place and keeps it alive. Params: ``to_map`` (a map's name),
  optional ``x`` and ``y`` (a tile on it; left out, anywhere on the map), ``dungeon`` (true: ``to_map`` is an instance's map, the party
  must go into it and then be brought back to where the escort began), ``scope`` (``"team"`` or ``"hero"`` with ``hero_id``: who counts as
  the escorted, whose fall fails the contract) and ``value``. The escorted team joins the escort's party when the contract is accepted and
  neither can leave it until the contract ends; their warps go only to places both have visited. See ``contracts.escort``. It is met when
  the party has arrived (and for a dungeon, returned), broken if the escorted fall in a fight after the contract started or time runs out.

A game adds kinds by subclassing ``StandardTerms`` and extending ``kinds``, ``validate``, ``value`` and ``check`` (calling ``super()`` for
the standard ones).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..delivery.models import CANCELLED, Shipment, ShipmentItem
from ..fights.events import EventType
from ..fights.models import FightActionRecord, FightParticipant, FightRecord
from ..heroes.models import Team, TeamMember
from ..models import Map
from .hooks import Contracts
from .models import BROKEN, MET, PENDING

TRUCE = "truce"
PARCEL = "parcel"
ASSASSINATE = "assassinate"
KILL_BOSS = "kill_boss"
ESCORT = "escort_to"
SCOPES = ("team", "hero")
PURPOSE = "contract"  # the parcel's purpose


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class StandardTerms(Contracts):
    def kinds(self) -> set[str]:
        return {TRUCE, PARCEL, ASSASSINATE, KILL_BOSS, ESCORT}

    def validate(self, kind: str, params: dict) -> str | None:
        if "value" in params and not _whole(params["value"]):
            return "a term's value is zero or more gold"
        if kind == PARCEL:
            gold, items = params.get("gold", 0), params.get("items", [])
            if not _whole(gold):
                return "a parcel's gold is zero or more"
            if not isinstance(items, list) or not all(
                isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and isinstance(row[1], int) and row[1] >= 1 for row in items
            ):
                return "a parcel's items are a list of [item key, quantity]"
            if not (gold or items):
                return "a parcel asks for gold or items"
        if kind == ASSASSINATE:
            if not (isinstance(params.get("target_team_id"), int) and not isinstance(params.get("target_team_id"), bool) and params["target_team_id"] >= 1):
                return "an assassination names a target team"
            if params.get("scope") not in SCOPES:
                return "an assassination kills the whole team or one hero of it"
            if params["scope"] == "hero" and not (isinstance(params.get("hero_id"), int) and not isinstance(params.get("hero_id"), bool) and params["hero_id"] >= 1):
                return "an assassination of one hero names the hero"
        if kind == ESCORT:
            if not (isinstance(params.get("to_map"), str) and 1 <= len(params["to_map"]) <= 64):
                return "an escort names a destination map"
            if ("x" in params) != ("y" in params) or not all(_whole(params.get(axis, 0)) for axis in ("x", "y")):
                return "an escort's tile is an x and a y, whole numbers"
            if "dungeon" in params and not isinstance(params["dungeon"], bool):
                return "an escort's dungeon is true or false"
            if params.get("scope") not in SCOPES:
                return "an escort keeps the whole team alive or one hero of it"
            if params["scope"] == "hero" and not (isinstance(params.get("hero_id"), int) and not isinstance(params.get("hero_id"), bool) and params["hero_id"] >= 1):
                return "an escort of one hero names the hero"
        if kind == KILL_BOSS and not (isinstance(params.get("monster"), str) and 1 <= len(params["monster"]) <= 64):
            return "a boss kill names the boss, a monster key"
        return None

    async def check_offer(self, session, a_team_id, b_team_id, my_terms, their_terms) -> str | None:
        for bound, terms in ((a_team_id, my_terms), (b_team_id, their_terms)):
            for kind, params in terms:
                if kind == ESCORT:
                    escorted = b_team_id if bound == a_team_id else a_team_id
                    if await session.scalar(select(Map.id).where(Map.name == params["to_map"]).limit(1)) is None:
                        return "there is no such destination map"
                    if params["scope"] == "hero" and await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == params["hero_id"])) != escorted:
                        return "that hero is not on the escorted team"
                    continue
                if kind != ASSASSINATE:
                    continue
                target = params["target_team_id"]
                if target in (a_team_id, b_team_id):
                    return "a team cannot be hired to kill itself or the team that hired it"
                if await session.get(Team, target) is None:
                    return "there is no such team to target"
                if params["scope"] == "hero":
                    team_of = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == params["hero_id"]))
                    if team_of != target:
                        return "that hero is not on the target team"
        return None

    def value(self, kind: str, params: dict) -> int:
        if "value" in params:
            return params["value"]
        return params.get("gold", 0) if kind == PARCEL else 0

    async def check(self, session: AsyncSession, contract, term, ended: bool) -> str:
        if term.kind == TRUCE:
            if await self._attacked(session, contract, term):
                return BROKEN
            return MET if ended else PENDING
        if term.kind == PARCEL:
            if await self._delivered(session, contract, term):
                return MET
            return BROKEN if ended else PENDING
        if term.kind == ASSASSINATE:
            if await self._assassinated(session, contract, term):
                return MET
            return BROKEN if ended else PENDING
        if term.kind == KILL_BOSS:
            if await self._boss_killed(session, contract, term):
                return MET
            return BROKEN if ended else PENDING
        if term.kind == ESCORT:
            if await self._escorted_fell(session, contract, term):
                return BROKEN
            if term.params.get("done"):
                return MET
            return BROKEN if ended else PENDING
        return await super().check(session, contract, term, ended)

    async def _heroes(self, session: AsyncSession, team_id: int) -> list[int]:
        return list((await session.scalars(select(TeamMember.hero_id).where(TeamMember.team_id == team_id))).all())

    def _sides(self, contract, term) -> tuple[int, int]:
        """The team the term binds and the other team."""
        return (contract.a_team_id, contract.b_team_id) if term.side == "a" else (contract.b_team_id, contract.a_team_id)

    async def _attacked(self, session: AsyncSession, contract, term) -> bool:
        bound, other = self._sides(contract, term)
        mine, theirs = await self._heroes(session, bound), await self._heroes(session, other)
        if not (mine and theirs):
            return False
        since = datetime.fromtimestamp(contract.starts_at, UTC)
        attacker, target = FightParticipant, FightParticipant.__table__.alias("target")
        found = await session.scalar(
            select(attacker.fight_id)
            .join(FightRecord, FightRecord.id == attacker.fight_id)
            .join(target, target.c.fight_id == attacker.fight_id)
            .where(
                FightRecord.created_at >= since, attacker.party == 0, attacker.hero_id.in_(mine),
                target.c.party != 0, target.c.hero_id.in_(theirs),
            )
            .limit(1)
        )
        return found is not None

    async def _delivered(self, session: AsyncSession, contract, term) -> bool:
        bound, other = self._sides(contract, term)
        shipments = list((await session.scalars(
            select(Shipment.id).where(
                Shipment.purpose == PURPOSE, Shipment.ref == str(contract.id), Shipment.from_team_id == bound,
                Shipment.to_team_id == other, Shipment.state != CANCELLED,
            )
        )).all())
        if not shipments:
            return False
        gold = (await session.scalars(select(Shipment.gold).where(Shipment.id.in_(shipments)))).all()
        if sum(gold) < term.params.get("gold", 0):
            return False
        sent = {}
        rows = await session.execute(
            select(Item.key, ShipmentItem.qty).join(Item, Item.id == ShipmentItem.item_id).where(ShipmentItem.shipment_id.in_(shipments))
        )
        for key, qty in rows.all():
            sent[key] = sent.get(key, 0) + qty
        return all(sent.get(key, 0) >= qty for key, qty in term.params.get("items", []))

    async def _assassinated(self, session: AsyncSession, contract, term) -> bool:
        bound, _other = self._sides(contract, term)
        target = term.params["target_team_id"]
        wanted = set(await self._heroes(session, target))
        if term.params["scope"] == "hero":
            wanted &= {term.params["hero_id"]}
        mine = await self._heroes(session, bound)
        if not (wanted and mine):
            return False
        since = datetime.fromtimestamp(contract.starts_at, UTC)
        # fights since the contract began that had a hero of the target and a hero of the bound side on opposite sides
        mine_in, theirs_in = FightParticipant, FightParticipant.__table__.alias("theirs")
        fights = (await session.scalars(
            select(mine_in.fight_id).join(FightRecord, FightRecord.id == mine_in.fight_id).join(theirs_in, theirs_in.c.fight_id == mine_in.fight_id)
            .where(FightRecord.created_at >= since, mine_in.hero_id.in_(mine), theirs_in.c.hero_id.in_(wanted), theirs_in.c.party != mine_in.party)
            .distinct()
        )).all()
        fallen: set[int] = set()
        for fight_id in fights:
            seats = {
                (party, group, character): hero_id
                for party, group, character, hero_id in (await session.execute(
                    select(FightParticipant.party, FightParticipant.group_index, FightParticipant.character, FightParticipant.hero_id)
                    .where(FightParticipant.fight_id == fight_id, FightParticipant.hero_id.in_(wanted))
                )).all()
            }
            for events in (await session.scalars(select(FightActionRecord.events).where(FightActionRecord.fight_id == fight_id))).all():
                for kind, data in events:
                    if kind == EventType.DIED.value and tuple(data[:3]) in seats:
                        fallen.add(seats[tuple(data[:3])])
        return wanted <= fallen

    async def _boss_killed(self, session: AsyncSession, contract, term) -> bool:
        map_id = term.params.get("entered_map_id")
        bound, _other = self._sides(contract, term)
        mine = await self._heroes(session, bound)
        if map_id is None or not mine:
            return False
        since = datetime.fromtimestamp(contract.starts_at, UTC)
        hero, boss = FightParticipant, FightParticipant.__table__.alias("boss")
        fights = (await session.scalars(
            select(hero.fight_id).join(FightRecord, FightRecord.id == hero.fight_id).join(boss, boss.c.fight_id == hero.fight_id)
            .where(FightRecord.created_at >= since, FightRecord.map_id == map_id, hero.hero_id.in_(mine), boss.c.monster_key == term.params["monster"])
            .distinct()
        )).all()
        for fight_id in fights:
            seats = {
                (party, group, character)
                for party, group, character in (await session.execute(
                    select(FightParticipant.party, FightParticipant.group_index, FightParticipant.character)
                    .where(FightParticipant.fight_id == fight_id, FightParticipant.monster_key == term.params["monster"])
                )).all()
            }
            for events in (await session.scalars(select(FightActionRecord.events).where(FightActionRecord.fight_id == fight_id))).all():
                if any(kind == EventType.DIED.value and tuple(data[:3]) in seats for kind, data in events):
                    return True
        return False

    async def _escorted_fell(self, session: AsyncSession, contract, term) -> bool:
        """Whether any of the escorted fell (a ``Died`` event) in a fight that began after the contract started."""
        escorted = contract.b_team_id if term.side == "a" else contract.a_team_id
        wanted = set(await self._heroes(session, escorted))
        if term.params["scope"] == "hero":
            wanted &= {term.params["hero_id"]}
        if not wanted:
            return False
        since = datetime.fromtimestamp(contract.starts_at, UTC)
        fights = (await session.scalars(
            select(FightParticipant.fight_id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
            .where(FightRecord.created_at >= since, FightParticipant.hero_id.in_(wanted)).distinct()
        )).all()
        for fight_id in fights:
            seats = {
                (party, group, character)
                for party, group, character in (await session.execute(
                    select(FightParticipant.party, FightParticipant.group_index, FightParticipant.character)
                    .where(FightParticipant.fight_id == fight_id, FightParticipant.hero_id.in_(wanted))
                )).all()
            }
            for events in (await session.scalars(select(FightActionRecord.events).where(FightActionRecord.fight_id == fight_id))).all():
                if any(kind == EventType.DIED.value and tuple(data[:3]) in seats for kind, data in events):
                    return True
        return False
