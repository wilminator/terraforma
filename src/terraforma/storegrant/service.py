"""The store's grant: Challenge Tokens and items put on (or taken off) an account by the game's store.

One function, ``grant``, behind the server-to-server call in ``storegrant.routes``. The game's rule (``Rules.store_grant``)
has the final say; the ledger of Challenge Tokens stays in ``terraforma.challenge`` and items land in the account's parcel.
Everything happens in the caller's transaction: if any part is refused, nothing changes.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..challenge import service as tokens
from ..content.models import Item
from ..fights.rules import Rules
from ..heroes.field import in_running_fight
from ..heroes.models import Hero
from ..models import Account
from ..storage import service as storage_service
from ..storage.hooks import Storage
from .models import Parcel, StoreGrant

REASON = "store"
MAX_PARCEL_QTY = 1_000_000  # of one item
MAX_ITEMS_PER_GRANT = 50


class GrantError(ValueError):
    """Base of what a grant refuses."""


class UnknownAccount(GrantError):
    """No such account."""


class UnknownItem(GrantError):
    """A key names no active item."""


class KeyReused(GrantError):
    """The idempotency key was used before for a different grant."""


class Refused(GrantError):
    """The game's rule said no (the message is its reason)."""


class ParcelFull(GrantError):
    """An item would pass MAX_PARCEL_QTY in the parcel."""


class _Taken(Exception):
    """The key was taken by a grant that committed between our look and our insert."""


@dataclass(frozen=True)
class Granted:
    """What a grant did: the account's token balance after it, and whether this repeated a grant already made (then nothing
    changed; ``message`` is what the first call sent, which the caller does not email again)."""

    balance: int
    duplicate: bool


def ledger_key(key: str) -> str:
    """The grant's key in the token ledger, apart from the purchase keys that share its unique column."""
    return f"store:{key}"


async def grant(
    session: AsyncSession,
    rules: Rules,
    account_id: int,
    token_change: int,
    items: dict[str, int],
    message: str,
    key: str,
) -> Granted:
    """Grants the account $token_change tokens (negative takes) and $items (content key -> quantity of at least 1) under the
    store's idempotency $key: sending the same key with the same grant again changes nothing and answers ``duplicate``; the
    same key for a different grant raises KeyReused. Raises UnknownAccount, UnknownItem, Refused (the game's rule said no),
    tokens.NotEnoughTokens (a spend below zero), tokens.PurseFull (a credit over the purse cap) and ParcelFull; nothing changes
    when it raises."""
    if len(items) > MAX_ITEMS_PER_GRANT or any(qty < 1 for qty in items.values()):
        raise GrantError("items need a quantity of at least 1 each, and at most %d kinds" % MAX_ITEMS_PER_GRANT)
    if token_change == 0 and not items:
        raise GrantError("a grant changes tokens or gives items")
    if await session.get(Account, account_id) is None:
        raise UnknownAccount("no such account")
    seen = await _grant_with_key(session, key)
    if seen is not None:
        return await _repeat(session, seen, account_id, token_change, items, message)
    rows = await _items_by_key(session, items)
    if (why := rules.store_grant(account_id, token_change, dict(items), message)) is not None:
        raise Refused(why)
    try:
        async with session.begin_nested():  # the record, the tokens and the parcel go together or not at all
            try:
                async with session.begin_nested():
                    session.add(StoreGrant(idempotency_key=key, account_id=account_id, tokens=token_change, items=dict(items), message=message))
                    await session.flush()
            except IntegrityError as error:  # a call with the same key got in first
                raise _Taken from error
            if token_change:
                await tokens.change(session, account_id, token_change, REASON, key=ledger_key(key), purse_cap=rules.challenge_purse_cap(account_id))
            for item_key, qty in sorted(items.items()):
                await _put_in_parcel(session, account_id, rows[item_key], qty)
    except _Taken:
        return await _repeat(session, await _grant_with_key(session, key), account_id, token_change, items, message)
    return Granted(await tokens.balance(session, account_id), duplicate=False)


async def parcel(session: AsyncSession, account_id: int) -> dict[str, int]:
    """What waits in the account's parcel: content key -> quantity."""
    found = await session.execute(
        select(Item.key, Parcel.qty).join(Item, Item.id == Parcel.item_id).where(Parcel.account_id == account_id, Parcel.qty > 0)
    )
    return {key: qty for key, qty in found.all()}


async def collect(session: AsyncSession, storage: Storage, account_id: int, hero: Hero, item_key: str, qty: int) -> dict:
    """Takes up to $qty of an item from the account's parcel to $hero (the game's storage first, then the packs it shares with:
    ``storage.service.deliver``); whatever does not fit stays in the parcel. Returns ``{"taken", "waiting", "stored", "packed"}``. Raises
    GrantError when the parcel holds none of the item or the hero is in a running fight; nothing changes then."""
    if await in_running_fight(session, hero):
        raise GrantError(f"{hero.name} is in a fight")
    row = await session.scalar(
        select(Parcel).join(Item, Item.id == Parcel.item_id).where(Parcel.account_id == account_id, Item.key == item_key, Parcel.qty > 0)
    )
    if row is None:
        raise GrantError("the parcel holds none of that")
    want = min(qty, row.qty)
    done = await storage_service.deliver(session, storage, hero, item_key, want)
    taken = want - done.left
    row.qty -= taken
    await session.flush()
    return {"taken": taken, "waiting": row.qty, "stored": done.stored, "packed": sum(done.packed.values())}


async def _repeat(session, seen: StoreGrant, account_id: int, token_change: int, items: dict[str, int], message: str) -> Granted:
    if (seen.account_id, seen.tokens, seen.items, seen.message) != (account_id, token_change, dict(items), message):
        raise KeyReused("that key was used for a different grant")
    return Granted(await tokens.balance(session, account_id), duplicate=True)


async def _grant_with_key(session: AsyncSession, key: str) -> StoreGrant | None:
    return await session.scalar(select(StoreGrant).where(StoreGrant.idempotency_key == key))


async def _items_by_key(session: AsyncSession, items: dict[str, int]) -> dict[str, Item]:
    found = {item.key: item for item in await session.scalars(select(Item).where(Item.key.in_(list(items)), Item.active.is_(True)))}
    if missing := sorted(set(items) - set(found)):
        raise UnknownItem(f"no such item: {', '.join(missing)}")
    return found


async def _put_in_parcel(session: AsyncSession, account_id: int, item: Item, qty: int) -> None:
    row = await session.scalar(select(Parcel).where(Parcel.account_id == account_id, Parcel.item_id == item.id))
    if row is None:
        session.add(Parcel(account_id=account_id, item_id=item.id, qty=qty))
    elif row.qty + qty > MAX_PARCEL_QTY:
        raise ParcelFull(f"the parcel cannot hold that many {item.name}")
    else:
        row.qty += qty
    await session.flush()
