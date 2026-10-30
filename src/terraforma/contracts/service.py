"""Contracts between two teams, secured by breach escrow.

Side ``a`` offers a contract to side ``b`` at a guild (``offer``): each side lists the terms it is bound to and the gold it will put down
as a breach escrow, and ``a`` pays the guild's fee and puts its escrow down at once. ``b`` answers (``accept`` or ``decline``) before the
offer runs out; accepting means paying their fee and putting their escrow down, and then the contract is active for its duration. ``a`` may
take an unanswered offer back (``withdraw``) and ``expire_offers`` clears the ones nobody answered. Fees are the game's base amount put
through its fee rule (``Game(fees=...)``), so a higher rank pays less, and each side pays at its own rank.

Escrow protects each side from the other's breach, and it has a floor: a side's escrow is at least ``Contracts.minimum_percent`` of the
other side's total at stake (the worth of their terms plus their escrow), checked for both sides when the offer is made.

Side ``a`` may also set a *payment*: a fee for ``b`` that is held from the offer and paid to ``b`` when the contract ends with none of
b's terms broken (returned to ``a`` if b broke one, or if the offer ends unanswered). It is a deposit of its own, apart from the escrows.

``judge`` asks the game's ``Contracts.check`` about every term still pending. A broken term ends the contract ``breached``: the escrow of
the side that broke it goes to the other side and the other's comes back (if both broke, each goes to the other). When time is up and
nothing is broken, or every term is met, it ends ``fulfilled`` and both escrows come back. Contracts never move items between teams; a
term that needs goods to change hands uses delivery (``terraforma.delivery``). Everything is all or nothing, and a contract is settled
once (``UPDATE ... WHERE state = 'active'``).
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..economy import Economy, NotEnoughGold
from ..escrow import service as escrow
from ..guild.fees import Fees
from ..heroes.models import Hero, Team, TeamMember
from ..storage.hooks import Storage
from .hooks import Contracts
from .models import (
    ACTIVE, BREACHED, BROKEN, DECLINED, EXPIRED, FULFILLED, MET, OFFERED, PENDING, SIDES, WITHDRAWN, Contract, ContractTerm,
)

PURPOSE = "contract"  # the escrow deposit's purpose
MAX_AMOUNT = 10**9
MAX_DURATION = 365 * 24 * 3600
MAX_TERMS = 20


class ContractError(ValueError):
    """Something the caller can fix: the message says what."""


async def _team_of(session: AsyncSession, hero: Hero) -> int:
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        raise ContractError("only a hero on a team can make a contract")
    return team_id


def _check_terms(contracts: Contracts, side: str, terms: list[tuple[str, dict]]) -> int:
    """The worth of one side's terms, after checking them."""
    if len(terms) > MAX_TERMS:
        raise ContractError(f"a side is bound by at most {MAX_TERMS} terms")
    worth = 0
    for kind, params in terms:
        if kind not in contracts.kinds():
            raise ContractError(f"there is no kind of term {kind!r}")
        if (why := contracts.validate(kind, params)) is not None:
            raise ContractError(why)
        worth += max(0, contracts.value(kind, params))
    return worth


def minimum_escrow(contracts: Contracts, guild: str, other_worth: int, other_escrow: int) -> int:
    """The least a side may put down, when the other side's terms are worth $other_worth and the other side puts down $other_escrow."""
    percent = contracts.minimum_percent(guild)
    return -(-percent * (other_worth + other_escrow) // 100)


async def _pay(session: AsyncSession, economy: Economy, hero: Hero, amount: int) -> None:
    if amount:
        try:
            await economy.debit(session, hero, amount)
        except NotEnoughGold as error:
            raise ContractError("not enough gold") from error


async def _hold(session: AsyncSession, economy: Economy, hero: Hero, contract: Contract, side: str, gold: int):
    if not gold:
        return None
    try:
        return await escrow.hold(session, economy, hero, contract.guild, PURPOSE, f"{contract.id}:{side}", gold=gold)
    except escrow.EscrowError as error:
        raise ContractError(str(error)) from error


async def offer(
    session: AsyncSession, economy: Economy, fees: Fees, contracts: Contracts, hero: Hero, guild: str, to_team_id: int,
    my_terms: list[tuple[str, dict]], their_terms: list[tuple[str, dict]], my_escrow: int, their_escrow: int, duration: int,
    payment_gold: int = 0,
) -> Contract:
    """$hero's team offers team $to_team_id a contract at $guild that runs $duration seconds once accepted. $my_terms bind the offering
    side and $their_terms the other; each is a list of (kind, params). Each side puts down gold as breach escrow, $my_escrow now and
    $their_escrow when they accept; both must meet the minimum. $payment_gold is held now and paid to the other side if they keep their
    terms (see the module). The offering side pays its fee now. Raises ContractError, changing
    nothing, for anything wrong or short."""
    if not guild:
        raise ContractError("a contract needs a guild")
    a_team = await _team_of(session, hero)
    if a_team == to_team_id:
        raise ContractError("a team cannot make a contract with itself")
    if await session.get(Team, to_team_id) is None:
        raise ContractError("there is no such team")
    if not (my_terms or their_terms):
        raise ContractError("a contract binds someone to something")
    if not 1 <= duration <= MAX_DURATION:
        raise ContractError("a contract runs from a second to a year")
    if not (0 <= my_escrow <= MAX_AMOUNT and 0 <= their_escrow <= MAX_AMOUNT and 0 <= payment_gold <= MAX_AMOUNT):
        raise ContractError("escrow and payment are zero or more gold")
    worth = {"a": _check_terms(contracts, "a", my_terms), "b": _check_terms(contracts, "b", their_terms)}
    if (why := await contracts.check_offer(session, a_team, to_team_id, my_terms, their_terms)) is not None:
        raise ContractError(why)
    put = {"a": my_escrow, "b": their_escrow}
    for side, other in (("a", "b"), ("b", "a")):
        least = minimum_escrow(contracts, guild, worth[other], put[other])
        if put[side] < least:
            raise ContractError(f"the {'offering' if side == 'a' else 'other'} side's escrow is at least {least} gold")
    fee = await fees.quote(session, a_team, guild, contracts.base_fee(guild), "contract")
    async with session.begin_nested():
        contract = Contract(
            guild=guild, a_team_id=a_team, b_team_id=to_team_id, a_escrow_gold=my_escrow, b_escrow_gold=their_escrow,
            payment_gold=payment_gold, duration=duration, offer_expires_at=wallclock.timestamp() + contracts.offer_lifetime(guild),
        )
        session.add(contract)
        await session.flush()
        for side, terms in (("a", my_terms), ("b", their_terms)):
            for kind, params in terms:
                session.add(ContractTerm(contract_id=contract.id, side=side, kind=kind, params=dict(params)))
        await _pay(session, economy, hero, fee)
        deposit = await _hold(session, economy, hero, contract, "a", my_escrow)
        contract.a_escrow_id = None if deposit is None else deposit.id
        payment = await _hold(session, economy, hero, contract, "p", payment_gold)
        contract.payment_escrow_id = None if payment is None else payment.id
        await session.flush()
    return contract


async def post_open(
    session: AsyncSession, contracts: Contracts, guild: str, terms: list[tuple[str, dict]], escrow_gold: int, reward_gold: int, duration: int,
) -> Contract:
    """The guild itself posts an open contract: whichever team takes it first (``accept``, which pays its fee and puts down $escrow_gold)
    is bound by $terms, kinds from ``Contracts.open_kinds``, for $duration seconds, and is paid $reward_gold if every term is met; if it
    breaks one, the guild keeps the escrow. It stays open for ``Contracts.offer_lifetime`` and ``expire_offers`` clears it. This is the
    game's call, not a player's. Raises ContractError, changing nothing, for anything wrong."""
    if not guild:
        raise ContractError("a contract needs a guild")
    if not terms:
        raise ContractError("a contract binds someone to something")
    if not 1 <= duration <= MAX_DURATION:
        raise ContractError("a contract runs from a second to a year")
    if not (0 <= escrow_gold <= MAX_AMOUNT and 0 <= reward_gold <= MAX_AMOUNT):
        raise ContractError("escrow and reward are zero or more gold")
    for kind, _params in terms:
        if kind not in contracts.open_kinds():
            raise ContractError(f"there is no kind of open term {kind!r}")
    _check_terms(contracts, "b", terms)
    contract = Contract(
        guild=guild, a_team_id=None, b_team_id=None, a_escrow_gold=0, b_escrow_gold=escrow_gold, reward_gold=reward_gold,
        duration=duration, offer_expires_at=wallclock.timestamp() + contracts.offer_lifetime(guild),
    )
    async with session.begin_nested():
        session.add(contract)
        await session.flush()
        for kind, params in terms:
            session.add(ContractTerm(contract_id=contract.id, side="b", kind=kind, params=dict(params)))
        await session.flush()
    return contract


async def open_offers(session: AsyncSession, guild: str | None = None, limit: int = 50) -> list[Contract]:
    """The open contracts nobody has taken yet and still in time, newest first, at $guild or (None) at every guild."""
    query = select(Contract).where(
        Contract.state == OFFERED, Contract.a_team_id.is_(None), Contract.b_team_id.is_(None), Contract.offer_expires_at > wallclock.timestamp()
    )
    if guild is not None:
        query = query.where(Contract.guild == guild)
    return list((await session.scalars(query.order_by(Contract.id.desc()).limit(max(1, min(limit, 100))))).all())


async def terms_of(session: AsyncSession, contract: Contract) -> list[ContractTerm]:
    return list((await session.scalars(select(ContractTerm).where(ContractTerm.contract_id == contract.id).order_by(ContractTerm.id))).all())


async def accept(session: AsyncSession, economy: Economy, fees: Fees, contracts: Contracts, hero: Hero, contract_id: int) -> Contract:
    """The asked team accepts: they pay their fee and put their escrow down, and the contract starts. Raises ContractError, changing
    nothing, if the offer is gone, was not made to the hero's team, or anything is short."""
    contract = await session.get(Contract, contract_id)
    if contract is None or contract.state != OFFERED or contract.offer_expires_at <= wallclock.timestamp():
        raise ContractError("that offer is gone")
    team_id = await _team_of(session, hero)
    is_open = contract.b_team_id is None
    if not is_open and contract.b_team_id != team_id:
        raise ContractError("that offer is not for your team")
    guild, b_team, b_gold, duration = contract.guild, team_id, contract.b_escrow_gold, contract.duration
    fee = await fees.quote(session, b_team, guild, contracts.base_fee(guild), "contract")
    async with session.begin_nested():
        now = wallclock.timestamp()
        claim = update(Contract).where(Contract.id == contract_id, Contract.state == OFFERED, Contract.offer_expires_at > now)
        if is_open:  # the first team to take it holds it: a second finds b_team_id set
            claim = claim.where(Contract.b_team_id.is_(None))
        claimed = await session.execute(claim.values(state=ACTIVE, starts_at=now, ends_at=now + duration, b_team_id=b_team))
        if claimed.rowcount == 0:
            raise ContractError("that offer is gone")
        await _pay(session, economy, hero, fee)
        deposit = await _hold(session, economy, hero, contract, "b", b_gold)
        if deposit is not None:
            await session.execute(update(Contract).where(Contract.id == contract_id).values(b_escrow_id=deposit.id))
    await session.refresh(contract)
    return contract


async def _close_offer(session: AsyncSession, economy: Economy, storage: Storage, contract_id: int, state: str) -> Contract:
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise ContractError("there is no such contract")
    a_escrow_id, payment_id = contract.a_escrow_id, contract.payment_escrow_id
    async with session.begin_nested():
        claimed = await session.execute(
            update(Contract).where(Contract.id == contract_id, Contract.state == OFFERED).values(state=state, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            raise ContractError("that offer is settled already")
        try:
            for deposit_id in (a_escrow_id, payment_id):
                if deposit_id is not None:
                    await escrow.refund(session, economy, storage, deposit_id)
        except escrow.EscrowError as error:
            raise ContractError(str(error)) from error
    await session.refresh(contract)
    return contract


async def decline(session: AsyncSession, economy: Economy, storage: Storage, hero: Hero, contract_id: int) -> Contract:
    """The asked team turns the offer down; the offering side's escrow comes back (its fee does not)."""
    contract = await session.get(Contract, contract_id)
    if contract is None or contract.b_team_id != await _team_of(session, hero):
        raise ContractError("that offer is not for your team")
    return await _close_offer(session, economy, storage, contract_id, DECLINED)


async def withdraw(session: AsyncSession, economy: Economy, storage: Storage, hero: Hero, contract_id: int) -> Contract:
    """The offering team takes an unanswered offer back; their escrow comes back (the fee does not)."""
    contract = await session.get(Contract, contract_id)
    if contract is None or contract.a_team_id != await _team_of(session, hero):
        raise ContractError("there is no such offer of yours")
    return await _close_offer(session, economy, storage, contract_id, WITHDRAWN)


async def expire_offers(session: AsyncSession, economy: Economy, storage: Storage, limit: int = 100) -> int:
    """Closes up to $limit offers nobody answered in time, returning the offering side's escrow; returns how many. A game calls it
    from its housekeeping."""
    due = list((await session.scalars(
        select(Contract.id).where(Contract.state == OFFERED, Contract.offer_expires_at <= wallclock.timestamp()).order_by(Contract.offer_expires_at).limit(limit)
    )).all())
    done = 0
    for contract_id in due:
        try:
            await _close_offer(session, economy, storage, contract_id, EXPIRED)
            done += 1
        except ContractError:
            continue
    return done


async def judge(session: AsyncSession, economy: Economy, storage: Storage, contracts: Contracts, contract_id: int) -> Contract:
    """Asks the game about every pending term of an active contract and settles it if that decides it (see the module). Does nothing to
    a contract that is not active, or whose terms are still being kept. Raises ContractError, changing nothing, if a payout cannot be
    made (a team with no hero); the contract stays active and is judged again next time."""
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise ContractError("there is no such contract")
    if contract.state != ACTIVE:
        return contract
    ended = contract.ends_at <= wallclock.timestamp()
    a_escrow_id, b_escrow_id, a_team, b_team = contract.a_escrow_id, contract.b_escrow_id, contract.a_team_id, contract.b_team_id
    payment_id = contract.payment_escrow_id
    reward = contract.reward_gold
    async with session.begin_nested():
        terms = await terms_of(session, contract)
        for term in terms:
            if term.state != PENDING:
                continue
            verdict = await contracts.check(session, contract, term, ended)
            if verdict == PENDING and ended:
                verdict = BROKEN
            if verdict in (MET, BROKEN):
                term.state = verdict
        await session.flush()
        broke = {term.side for term in terms if term.state == BROKEN}
        if not broke and not (ended or all(term.state == MET for term in terms)):
            return contract
        state = BREACHED if broke else FULFILLED
        breached_by = "".join(side for side in SIDES if side in broke) or None
        claimed = await session.execute(
            update(Contract).where(Contract.id == contract_id, Contract.state == ACTIVE)
            .values(state=state, breached_by=breached_by, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            return contract
        try:
            for side, escrow_id, own, other in (("a", a_escrow_id, a_team, b_team), ("b", b_escrow_id, b_team, a_team)):
                if escrow_id is None:
                    continue
                if side in broke and other is None:  # an open contract: the guild keeps what the breaker put down
                    await escrow.keep(session, escrow_id)
                elif side in broke:
                    await escrow.forfeit(session, economy, storage, escrow_id, other)
                else:
                    await escrow.refund(session, economy, storage, escrow_id)
            if payment_id is not None:  # the fee: to b if b kept every term, else back to a
                if "b" in broke:
                    await escrow.refund(session, economy, storage, payment_id)
                else:
                    await escrow.forfeit(session, economy, storage, payment_id, b_team)
            if a_team is None and not broke and reward:
                await economy.credit_team(session, b_team, reward)
        except escrow.EscrowError as error:
            raise ContractError(str(error)) from error
    await session.refresh(contract)
    return contract
