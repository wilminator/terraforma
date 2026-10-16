"""The contract terms the engine ships: the standard set, and the base for a game that adds its own.

``StandardTerms`` is the default ``Game(contracts=...)``. Its kinds are the ones the engine can judge from its own records:

* ``truce``: the bound side does not pick a fight with the other side. It is broken by any fight that began after the contract started
  in which the bound side's heroes were the attacking party (party 0, as ``pvp.service.start_pvp_fight`` seats them) against the other
  side's heroes, and it is met when the contract's time runs out. Params: ``value``, the gold it is worth (default 0).
* ``parcel``: the bound side sends the other side goods by delivery (``terraforma.delivery``). It is met once parcels the bound side sent
  to the other side with the contract's reference (purpose ``contract``, ref the contract's id as text) hold at least the gold and items
  asked for, and broken if time runs out first. Params: ``gold`` and ``items`` (a list of [item key, quantity]); ``value`` is the gold it
  is worth (the gold asked for, by default). A parcel the game called back (cancelled) does not count.

A game adds kinds by subclassing ``StandardTerms`` and extending ``kinds``, ``validate``, ``value`` and ``check`` (calling ``super()`` for
the standard ones).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..delivery.models import CANCELLED, Shipment, ShipmentItem
from ..fights.models import FightParticipant, FightRecord
from ..heroes.models import TeamMember
from .hooks import Contracts
from .models import BROKEN, MET, PENDING

TRUCE = "truce"
PARCEL = "parcel"
PURPOSE = "contract"  # the parcel's purpose


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class StandardTerms(Contracts):
    def kinds(self) -> set[str]:
        return {TRUCE, PARCEL}

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
