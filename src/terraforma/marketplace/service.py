"""Listing goods at a guild for a fixed price, finding them, buying them, taking them back.

A hero posts a listing (``post``): the goods leave their pack for an escrow deposit at the guild, the posting fee leaves their purse and
the listing is open for the game's ``Marketplace.lifetime``. Anyone but the seller's team can ``buy`` it while it is open: the price
moves to the seller's team and the goods go to the buyer's team as a parcel (``delivery``) to the guild the buyer names, after the
route's delay and for its fee. Goods are never handed over directly, only delivered. A listing nobody buys goes back to its seller,
when they ``cancel`` it or when ``expire_due`` finds it out of time.

``search`` finds open listings at one guild or across all of them, by item name or key and a top price.

Everything is all or nothing, and a sale is claimed with a single ``UPDATE ... WHERE state = 'open'``, so two buyers at the same instant
never both get it. Fees are the game's base amounts put through ``Fees.quote`` (``guild.fees``).
"""

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..challenge import service as challenge
from ..content.models import Item
from ..delivery import service as delivery
from ..delivery.models import Shipment
from ..economy import Economy, NotEnoughGold
from ..escrow import service as escrow
from ..escrow.models import EscrowItem
from ..guild.fees import Fees
from ..heroes.models import Hero, Team, TeamMember
from ..storage.hooks import Storage
from .hooks import Marketplace
from .models import CANCELLED, EXPIRED, OPEN, SOLD, Listing

PURPOSE = "listing"  # the escrow deposit's purpose
PURCHASE = "purchase"  # the parcel's purpose
SALE = "sale"  # the reason in the Challenge Token ledger
MAX_PRICE = 10**9
MAX_PAGE = 100


class MarketplaceError(ValueError):
    """Something the caller can fix: the message says what."""


async def _team_of(session: AsyncSession, hero: Hero) -> int:
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        raise MarketplaceError("only a hero on a team can trade here")
    return team_id


async def _charge(session: AsyncSession, economy: Economy, hero: Hero, amount: int) -> None:
    if amount:
        try:
            await economy.debit(session, hero, amount)
        except NotEnoughGold as error:
            raise MarketplaceError("not enough gold") from error


async def post(
    session: AsyncSession, economy: Economy, fees: Fees, marketplace: Marketplace, hero: Hero, guild: str,
    items: list[tuple[int, int]], price_gold: int = 0, price_tokens: int = 0,
) -> Listing:
    """$hero lists $items, (pack position, quantity) pairs, at $guild for $price_gold gold and $price_tokens Challenge Tokens. The posting
    fee comes out of their purse. Raises MarketplaceError, changing nothing, for no goods, no price, a price out of range, anything the
    hero does not have or a fee they cannot pay."""
    if not guild:
        raise MarketplaceError("a listing needs a guild")
    if not items:
        raise MarketplaceError("there is nothing to list")
    if not (0 <= price_gold <= MAX_PRICE and 0 <= price_tokens <= MAX_PRICE) or not (price_gold or price_tokens):
        raise MarketplaceError("a listing asks for some gold or tokens, and no more than a billion of either")
    team_id = await _team_of(session, hero)
    fee = await fees.quote(session, team_id, guild, marketplace.listing_fee(guild, price_gold, price_tokens), "listing")
    async with session.begin_nested():
        listing = Listing(
            guild=guild, seller_team_id=team_id, price_gold=price_gold, price_tokens=price_tokens,
            expires_at=wallclock.timestamp() + marketplace.lifetime(guild),
        )
        session.add(listing)
        await session.flush()
        await _charge(session, economy, hero, fee)
        try:
            deposit = await escrow.hold(session, economy, hero, guild, PURPOSE, str(listing.id), items=items)
        except escrow.EscrowError as error:
            raise MarketplaceError(str(error)) from error
        listing.escrow_id = deposit.id
        await session.flush()
    return listing


async def goods(session: AsyncSession, listing: Listing) -> list[tuple[str, int]]:
    """What a listing offers, as (item key, quantity) pairs."""
    if listing.escrow_id is None:
        return []
    rows = await session.execute(
        select(Item.key, EscrowItem.qty).join(Item, Item.id == EscrowItem.item_id).where(EscrowItem.escrow_id == listing.escrow_id).order_by(EscrowItem.id)
    )
    return [(key, qty) for key, qty in rows.all()]


async def search(
    session: AsyncSession, guild: str | None = None, text: str | None = None, max_gold: int | None = None,
    limit: int = 20, offset: int = 0,
) -> list[dict]:
    """The open listings, cheapest gold price first, at $guild or (None) at every guild, only those with an item whose name or key
    contains $text, only those asking $max_gold or less in gold. Each is a dict with its id, guild, seller team name, prices, goods and
    the time it closes."""
    limit = max(1, min(limit, MAX_PAGE))
    query = select(Listing, Team.name).join(Team, Team.id == Listing.seller_team_id).where(
        Listing.state == OPEN, Listing.expires_at > wallclock.timestamp()
    )
    if guild is not None:
        query = query.where(Listing.guild == guild)
    if max_gold is not None:
        query = query.where(Listing.price_gold <= max_gold)
    if text:
        like = f"%{text.lower()}%"
        matching = (
            select(EscrowItem.escrow_id).join(Item, Item.id == EscrowItem.item_id)
            .where(or_(func.lower(Item.name).like(like), func.lower(Item.key).like(like)))
        )
        query = query.where(Listing.escrow_id.in_(matching))
    rows = (await session.execute(query.order_by(Listing.price_gold, Listing.id).limit(limit).offset(max(0, offset)))).all()
    return [
        {
            "id": listing.id, "guild": listing.guild, "seller": name, "price_gold": listing.price_gold,
            "price_tokens": listing.price_tokens, "goods": await goods(session, listing), "expires_at": listing.expires_at,
        }
        for listing, name in rows
    ]


async def buy(
    session: AsyncSession, economy: Economy, fees: Fees, marketplace: Marketplace, hero: Hero, listing_id: int, to_guild: str,
) -> Shipment:
    """$hero buys the open listing for their team and has the goods delivered to $to_guild. They pay its price and the delivery fee
    for the route from the listing's guild; the seller's team gets the price. Returns the parcel. Raises MarketplaceError, changing
    nothing, if the listing is gone, out of time or the buyer's own team's, or anything is short."""
    if not to_guild:
        raise MarketplaceError("say which guild the goods should be delivered to")
    buyer_team = await _team_of(session, hero)
    listing = await session.get(Listing, listing_id)
    if listing is None or listing.state != OPEN or listing.expires_at <= wallclock.timestamp():
        raise MarketplaceError("that listing is gone")
    if listing.seller_team_id == buyer_team:
        raise MarketplaceError("a team cannot buy its own listing")
    seller_team, guild, price_gold, price_tokens, escrow_id = listing.seller_team_id, listing.guild, listing.price_gold, listing.price_tokens, listing.escrow_id
    route = marketplace.delivery(guild, to_guild)
    fee = await fees.quote(session, buyer_team, to_guild, route.fee, "delivery")
    async with session.begin_nested():
        now = wallclock.timestamp()
        claimed = await session.execute(
            update(Listing).where(Listing.id == listing_id, Listing.state == OPEN, Listing.expires_at > now)
            .values(state=SOLD, buyer_team_id=buyer_team, settled_at=now)
        )
        if claimed.rowcount == 0:
            raise MarketplaceError("that listing is gone")
        await _charge(session, economy, hero, price_gold + fee)
        if price_tokens:
            try:
                await challenge.change(session, hero.account_id, -price_tokens, SALE)
            except challenge.NotEnoughTokens as error:
                raise MarketplaceError("not enough tokens") from error
        seller = await session.get(Team, seller_team)
        if price_gold:
            await economy.credit_team(session, seller_team, price_gold)
        if price_tokens:
            await challenge.change(session, seller.account_id, price_tokens, SALE)
        try:
            contents = await escrow.release(session, escrow_id, buyer_team)
            parcel = await delivery.send_goods(
                session, buyer_team, to_guild, PURCHASE, str(listing_id), delay=route.delay, items=contents["items"]
            )
        except (escrow.EscrowError, delivery.DeliveryError) as error:
            raise MarketplaceError(str(error)) from error
    return parcel


async def _take_back(session: AsyncSession, economy: Economy, storage: Storage, listing_id: int, state: str) -> Listing:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise MarketplaceError("there is no such listing")
    escrow_id = listing.escrow_id
    async with session.begin_nested():
        claimed = await session.execute(update(Listing).where(Listing.id == listing_id, Listing.state == OPEN).values(state=state, settled_at=wallclock.timestamp()))
        if claimed.rowcount == 0:
            raise MarketplaceError("that listing is settled already")
        try:
            await escrow.refund(session, economy, storage, escrow_id)
        except escrow.EscrowError as error:
            raise MarketplaceError(str(error)) from error
    await session.refresh(listing)
    return listing


async def cancel(session: AsyncSession, economy: Economy, storage: Storage, hero: Hero, listing_id: int) -> Listing:
    """The seller's team takes an open listing back: the goods return to their pack (they need room for all of it). The posting fee is
    not refunded. Raises MarketplaceError, changing nothing, for someone else's listing or one that is settled already."""
    listing = await session.get(Listing, listing_id)
    if listing is None or listing.seller_team_id != await _team_of(session, hero):
        raise MarketplaceError("there is no such listing of yours")
    return await _take_back(session, economy, storage, listing_id, CANCELLED)


async def expire_due(session: AsyncSession, economy: Economy, storage: Storage, limit: int = 100) -> int:
    """Gives the goods of up to $limit listings that ran out of time back to their sellers; returns how many it did. A listing whose
    seller has no room for the goods stays as it is (nobody can buy it any more) and is tried again next time."""
    due = list((await session.scalars(
        select(Listing.id).where(Listing.state == OPEN, Listing.expires_at <= wallclock.timestamp()).order_by(Listing.expires_at).limit(limit)
    )).all())
    done = 0
    for listing_id in due:
        try:
            await _take_back(session, economy, storage, listing_id, EXPIRED)
            done += 1
        except MarketplaceError:
            continue
    return done
